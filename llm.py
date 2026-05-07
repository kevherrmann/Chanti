"""LLM mit OpenAI-kompatibler Chat-Completions-API und Tool-Calling Support."""

import json
import logging
import random
import re
import time
from typing import Any

import requests

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_MODEL_TOOLS,
    LLM_PROVIDER,
)

logger = logging.getLogger("chanti")

LLM_URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"

# Tuning
MAX_TOOL_ROUNDS = 12

# Wenn dieselbe (tool, args)-Kombi so oft hintereinander vom Modell vorgeschlagen
# wird, brechen wir ab.
LOOP_DETECTION_REPEATS = 3

# Wenn dasselbe Tool mehrfach in Folge aufgerufen wird, ist das Modell wahrscheinlich
# in einem Trial-and-Error-Loop.
LOOP_DETECTION_SAME_TOOL = 5

REQUEST_TIMEOUT = 30
MAX_RETRIES_ON_429 = 3
MAX_RETRIES_ON_5XX = 2

DEFAULT_MAX_TOKENS = 1024
RAW_MAX_TOKENS = 50


def _redact(obj: Any) -> str:
    """Gibt Tool-Argumente für Logs zurück, kürzt lange Strings."""
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(obj)

    if len(s) > 300:
        return s[:300] + "…"

    return s


def _balanced_json_from_text(text: str) -> dict | None:
    """Extrahiert das erste balancierte JSON-Objekt aus Text."""
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    return None

    return None


def _parse_failed_generation(failed_gen: str, executors: dict | None) -> str | None:
    """
    Fallback, falls ein Modell Tool-Calls im falschen Format generiert.

    Erwartet grob entweder:
    - JSON mit name/arguments
    - Text, aus dem ein JSON-Objekt extrahierbar ist
    """
    if not failed_gen or not executors:
        return None

    parsed = _balanced_json_from_text(failed_gen)
    if not parsed:
        return None

    fn_name = (
        parsed.get("name")
        or parsed.get("tool")
        or parsed.get("function")
        or parsed.get("function_name")
    )

    args = parsed.get("arguments") or parsed.get("args") or {}

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if not isinstance(fn_name, str) or fn_name not in executors:
        return None

    if not isinstance(args, dict):
        args = {}

    try:
        return str(executors[fn_name](**args))
    except Exception as e:
        logger.error(
            "Fallback-Tool %s fehlgeschlagen: %s: %s",
            fn_name,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return f"Tool-Fehler: {fn_name} ist fehlgeschlagen ({type(e).__name__})."


def _llm_request(payload: dict, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY fehlt. Bitte .env prüfen.")

    return requests.post(
        LLM_URL,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )


def _request_with_retries(payload: dict) -> requests.Response | None:
    """Führt einen LLM-Request aus und retried bei 429 / 5xx mit Backoff."""
    attempt_429 = 0
    attempt_5xx = 0

    while True:
        try:
            resp = _llm_request(payload)
        except requests.exceptions.Timeout:
            logger.error("%s Timeout", LLM_PROVIDER)
            return None
        except requests.exceptions.ConnectionError:
            logger.error("%s nicht erreichbar", LLM_PROVIDER)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("%s Netzwerkfehler: %s: %s", LLM_PROVIDER, type(e).__name__, e)
            return None
        except RuntimeError as e:
            logger.error(str(e))
            return None

        if resp.status_code == 429 and attempt_429 < MAX_RETRIES_ON_429:
            attempt_429 += 1
            retry_after = resp.headers.get("Retry-After")

            try:
                wait = float(retry_after) if retry_after else 2.0 * attempt_429
            except ValueError:
                wait = 2.0 * attempt_429

            wait = min(wait, 10.0) + random.uniform(0, 0.5)
            logger.warning(
                "%s 429, retry in %.1fs (Versuch %s/%s)",
                LLM_PROVIDER,
                wait,
                attempt_429,
                MAX_RETRIES_ON_429,
            )
            time.sleep(wait)
            continue

        if 500 <= resp.status_code < 600 and attempt_5xx < MAX_RETRIES_ON_5XX:
            attempt_5xx += 1
            wait = 1.0 * attempt_5xx + random.uniform(0, 0.5)
            logger.warning(
                "%s %s, retry in %.1fs (Versuch %s/%s)",
                LLM_PROVIDER,
                resp.status_code,
                wait,
                attempt_5xx,
                MAX_RETRIES_ON_5XX,
            )
            time.sleep(wait)
            continue

        return resp


def _extract_content(resp_json: dict) -> str:
    try:
        return (resp_json["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _tool_signature(fn_name: str, fn_args: dict) -> str:
    """Signatur für Loop-Detection. Gleiche Signatur = gleicher Aufruf."""
    try:
        args_str = json.dumps(fn_args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        args_str = repr(fn_args)

    return f"{fn_name}::{args_str}"


def _sanitize_for_no_tools(messages: list[dict]) -> list[dict]:
    """
    Macht eine Message-Liste kompatibel zu einem Request ohne Tools.

    - assistant-Messages mit tool_calls werden in reinen Text umgewandelt.
    - role=tool Messages werden in user-Messages umgewandelt.
    """
    out: list[dict] = []

    for msg in messages:
        role = msg.get("role")

        if role == "assistant" and msg.get("tool_calls"):
            calls = msg.get("tool_calls") or []
            names = [tc.get("function", {}).get("name", "?") for tc in calls]
            summary = f"[Intern: Tools aufgerufen: {', '.join(names)}]"
            existing = (msg.get("content") or "").strip()
            text = (existing + "\n" + summary).strip() if existing else summary
            out.append({"role": "assistant", "content": text})

        elif role == "tool":
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            out.append(
                {
                    "role": "user",
                    "content": f"[Ergebnis von {name}]: {content}",
                }
            )

        else:
            out.append(msg)

    return out


def _error_message_from_response(resp: requests.Response) -> str:
    try:
        error_data = resp.json().get("error", {}) or {}
    except ValueError:
        error_data = {}

    message = error_data.get("message") or resp.text[:300]

    logger.warning(
        "%s Fehler %s: %s",
        LLM_PROVIDER,
        resp.status_code,
        message[:300],
    )

    if resp.status_code == 401:
        return (
            "Entschuldigung Kevin, der DeepSeek/API-Key wurde abgelehnt. "
            "Bitte prüfe LLM_API_KEY in der .env."
        )

    if resp.status_code == 402:
        return (
            "Entschuldigung Kevin, der API-Anbieter meldet fehlendes Guthaben "
            "oder ein Billing-Problem."
        )

    if resp.status_code == 413:
        return (
            "Entschuldigung Kevin, die Anfrage war zu groß für das Modell. "
            "Starte am besten eine neue Unterhaltung oder kürze den Kontext."
        )

    if resp.status_code == 429:
        return (
            "Entschuldigung Kevin, das Modell ist gerade im Rate Limit. "
            "Versuch es gleich nochmal."
        )

    return "Entschuldigung Kevin, da ist beim LLM-Anbieter etwas schiefgelaufen."


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    executors: dict | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    local_messages = list(messages)

    model = LLM_MODEL_TOOLS if tools else LLM_MODEL

    payload = {
        "model": model,
        "messages": local_messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = False

    logger.info("Provider: %s | Modell: %s | Endpoint: %s", LLM_PROVIDER, model, LLM_URL)

    repeat_counts: dict[str, int] = {}
    last_tool_name: str | None = None
    consecutive_same_tool = 0

    for round_num in range(MAX_TOOL_ROUNDS):
        resp = _request_with_retries(payload)

        if resp is None:
            return f"Entschuldigung Kevin, ich kann {LLM_PROVIDER} gerade nicht erreichen."

        if not resp.ok:
            error_data = {}
            try:
                error_data = resp.json().get("error", {}) or {}
            except ValueError:
                pass

            failed_gen = error_data.get("failed_generation", "")

            if failed_gen and executors:
                tool_result = _parse_failed_generation(failed_gen, executors)
                if tool_result:
                    logger.info("Fallback erfolgreich, sende Ergebnis zurück")
                    fallback_resp = _request_with_retries(
                        {
                            "model": LLM_MODEL,
                            "messages": list(local_messages)
                            + [
                                {
                                    "role": "user",
                                    "content": (
                                        f"[Tool-Ergebnis]: {tool_result}\n\n"
                                        "Bitte antworte basierend auf diesem Ergebnis."
                                    ),
                                }
                            ],
                            "temperature": 0.7,
                            "max_tokens": max_tokens,
                        }
                    )

                    if fallback_resp is not None and fallback_resp.ok:
                        return _extract_content(fallback_resp.json())

            if tools:
                logger.info("Versuche ohne Tools…")
                sanitized = _sanitize_for_no_tools(local_messages)
                fallback_resp = _request_with_retries(
                    {
                        "model": LLM_MODEL,
                        "messages": sanitized,
                        "temperature": 0.7,
                        "max_tokens": max_tokens,
                    }
                )

                if fallback_resp is not None and fallback_resp.ok:
                    return _extract_content(fallback_resp.json())

            return _error_message_from_response(resp)

        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError) as e:
            logger.error("Ungültige LLM-Antwort: %s", e)
            return f"Entschuldigung Kevin, die Antwort von {LLM_PROVIDER} war unvollständig."

        finish_reason = choice.get("finish_reason", "")

        if finish_reason != "tool_calls":
            return (message.get("content") or "").strip()

        tool_calls = message.get("tool_calls", []) or []

        if not tool_calls:
            return (message.get("content") or "").strip()

        assistant_msg = {"role": "assistant", "tool_calls": tool_calls}

        if message.get("content"):
            assistant_msg["content"] = message["content"]

        local_messages.append(assistant_msg)
        payload["messages"] = local_messages

        for tc in tool_calls:
            fn_name = tc.get("function", {}).get("name", "?")
            raw_args = tc.get("function", {}).get("arguments", "{}")

            try:
                fn_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                if not isinstance(fn_args, dict):
                    raise TypeError("arguments ist kein dict")

                args_ok = True

            except (json.JSONDecodeError, TypeError) as e:
                fn_args = {}
                args_ok = False
                logger.warning("Tool-Args nicht parsebar für %s: %s", fn_name, e)

            logger.info("Tool-Call #%s: %s(%s)", round_num + 1, fn_name, _redact(fn_args))

            if fn_name == last_tool_name:
                consecutive_same_tool += 1
            else:
                consecutive_same_tool = 1

            last_tool_name = fn_name

            if consecutive_same_tool >= LOOP_DETECTION_SAME_TOOL:
                logger.warning(
                    "Loop erkannt: %s %sx in Folge — breche ab",
                    fn_name,
                    consecutive_same_tool,
                )
                local_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": fn_name,
                        "content": (
                            f"Tool-Aufruf abgebrochen: du hast {fn_name} "
                            f"{consecutive_same_tool} mal in Folge aufgerufen. "
                            "Wahrscheinlich ist der Ansatz falsch. Probiere ein "
                            "anderes Tool oder sag dem User was nicht geht."
                        ),
                    }
                )
                return _final_answer_without_tools(local_messages, max_tokens)

            sig = _tool_signature(fn_name, fn_args) if args_ok else None

            if sig is not None:
                repeat_counts[sig] = repeat_counts.get(sig, 0) + 1

                if repeat_counts[sig] >= LOOP_DETECTION_REPEATS:
                    logger.warning(
                        "Loop erkannt: %s mit identischen Args %sx aufgerufen — breche ab",
                        fn_name,
                        repeat_counts[sig],
                    )
                    local_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "name": fn_name,
                            "content": (
                                f"Tool-Aufruf abgebrochen: du hast {fn_name} "
                                f"bereits {repeat_counts[sig]} mal mit identischen "
                                "Argumenten aufgerufen. Der Ansatz funktioniert "
                                "offensichtlich nicht. Versuche etwas anderes "
                                "oder sag dem User direkt, dass es nicht klappt."
                            ),
                        }
                    )
                    return _final_answer_without_tools(local_messages, max_tokens)

            if not args_ok:
                result = (
                    f"Tool-Aufruf abgelehnt: Argumente für {fn_name} "
                    "waren nicht als JSON parsebar. Bitte erneut mit "
                    "gültigen JSON-Argumenten aufrufen."
                )

            elif executors and fn_name in executors:
                try:
                    result = executors[fn_name](**fn_args)
                except TypeError as e:
                    logger.warning("Tool %s TypeError: %s", fn_name, e)
                    result = f"Tool-Fehler: ungültige Parameter ({e})."
                except Exception as e:
                    logger.error(
                        "Tool %s Exception: %s: %s",
                        fn_name,
                        type(e).__name__,
                        e,
                        exc_info=True,
                    )
                    result = (
                        f"Tool-Fehler: {fn_name} hat einen internen Fehler "
                        f"({type(e).__name__}). Sag Kevin bitte direkt, "
                        "dass das Tool fehlgeschlagen ist."
                    )

            else:
                result = f"Tool '{fn_name}' nicht verfügbar."

            logger.debug("Ergebnis: %s", str(result)[:300])

            local_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn_name,
                    "content": str(result),
                }
            )

    logger.warning("MAX_TOOL_ROUNDS (%s) erreicht", MAX_TOOL_ROUNDS)
    return _final_answer_without_tools(local_messages, max_tokens)


def _final_answer_without_tools(messages: list[dict], max_tokens: int) -> str:
    """
    Gibt dem Modell eine letzte Chance, einen Abschluss-Satz zu formulieren,
    ohne weitere Tools aufzurufen.
    """
    hint = {
        "role": "user",
        "content": (
            "Das Tool-Budget ist aufgebraucht. Fasse in 1–3 Sätzen zusammen, "
            "was du versucht hast und was davon hat/nicht geklappt. "
            "Rufe KEIN weiteres Tool mehr auf."
        ),
    }

    sanitized = _sanitize_for_no_tools(messages)

    resp = _request_with_retries(
        {
            "model": LLM_MODEL,
            "messages": sanitized + [hint],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
    )

    if resp is not None and resp.ok:
        content = _extract_content(resp.json())
        if content:
            return content

    return "Ich konnte die Anfrage nicht abschließen — zu viele Tool-Runden."


def raw_chat(prompt: str) -> str:
    """Einfacher Chat ohne Tools. Mit Error-Handling und Retry."""
    resp = _request_with_retries(
        {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": RAW_MAX_TOKENS,
        }
    )

    if resp is None or not resp.ok:
        if resp is not None:
            logger.error("raw_chat Fehler: %s", resp.status_code)
        return ""

    try:
        return _extract_content(resp.json())
    except Exception as e:
        logger.error("raw_chat Parse-Fehler: %s", e)
        return ""
