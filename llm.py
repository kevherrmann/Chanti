"""LLM via Groq mit Tool-Calling Support."""
import json
import logging
import random
import re
import time

import requests

logger = logging.getLogger("chanti")

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_MODEL_TOOLS,
)

LLM_URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"

# Tuning
MAX_TOOL_ROUNDS = 12
# Wenn dieselbe (tool, args)-Kombi so oft hintereinander vom Modell vorgeschlagen
# wird, brechen wir ab — das Modell ist im Kreis. 3 reicht: 1x probieren,
# 1x nach Fehler-Hinweis nochmal probieren, beim 3. Mal ist klar dass der
# Fehlermeldungs-Text nicht hilft.
LOOP_DETECTION_REPEATS = 3
# Fuzzy-Variante: wenn dasselbe Tool N× hintereinander aufgerufen wird
# (egal mit welchen Args), ist das Modell wahrscheinlich im Trial-and-Error-
# Loop. 5 lässt legitime Workflows durch (z.B. 5× workspace_edit für
# verschiedene Dateien) aber fängt die 8×terminal-Stolperfallen ab.
LOOP_DETECTION_SAME_TOOL = 5
REQUEST_TIMEOUT = 30
MAX_RETRIES_ON_429 = 3
MAX_RETRIES_ON_5XX = 2
DEFAULT_MAX_TOKENS = 1024
RAW_MAX_TOKENS = 50


def _redact(obj) -> str:
    """Gibt Tool-Argumente für Logs zurück, kürzt lange Strings."""
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > 300:
        return s[:300] + "…"
    return s


def _parse_failed_generation(failed_gen: str, executors: dict) -> str | None:
    """Fallback falls Modell Tool-Call im falschen Format (XML-ähnlich) generiert.
    Versucht das JSON-Objekt mit balanced-brace-Logik zu extrahieren."""
    m = re.search(r"<function=(\w+)\s*(\{)", failed_gen, re.DOTALL)
    if not m:
        return None
    fn_name = m.group(1)
    start = m.start(2)

    # Balanced braces parsen, damit verschachtelte Objekte mitgenommen werden.
    depth = 0
    end = None
    in_str = False
    escape = False
    for i in range(start, len(failed_gen)):
        c = failed_gen[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    args_str = failed_gen[start:end]
    try:
        fn_args = json.loads(args_str)
    except json.JSONDecodeError:
        return None

    logger.info(f"Fallback Tool-Call: {fn_name}({_redact(fn_args)})")
    if fn_name in executors:
        try:
            return str(executors[fn_name](**fn_args))
        except Exception as e:
            logger.warning(f"Fallback-Tool {fn_name} failed: {type(e).__name__}: {e}")
            return f"[Tool-Fehler: {type(e).__name__}]"
    return None


def _llm_request(payload: dict, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    return requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        json=payload,
        timeout=timeout,
    )


def _request_with_retries(payload: dict) -> requests.Response | None:
    """Führt einen Groq-Request aus und retried bei 429 / 5xx mit Backoff.
    Gibt Response zurück (auch 4xx-Fehler), oder None bei Netzwerkfehler."""
    attempt_429 = 0
    attempt_5xx = 0
    while True:
        try:
            resp = _llm_request(payload)
        except requests.exceptions.Timeout:
            logger.error("Groq Timeout")
            return None
        except requests.exceptions.ConnectionError:
            logger.error("Groq nicht erreichbar")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Groq Netzwerkfehler: {type(e).__name__}: {e}")
            return None

        if resp.status_code == 429 and attempt_429 < MAX_RETRIES_ON_429:
            attempt_429 += 1
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else 2.0 * attempt_429
            except ValueError:
                wait = 2.0 * attempt_429
            wait = min(wait, 10.0) + random.uniform(0, 0.5)
            logger.warning(f"Groq 429, retry in {wait:.1f}s (Versuch {attempt_429}/{MAX_RETRIES_ON_429})")
            time.sleep(wait)
            continue

        if 500 <= resp.status_code < 600 and attempt_5xx < MAX_RETRIES_ON_5XX:
            attempt_5xx += 1
            wait = 1.0 * attempt_5xx + random.uniform(0, 0.5)
            logger.warning(f"Groq {resp.status_code}, retry in {wait:.1f}s "
                           f"(Versuch {attempt_5xx}/{MAX_RETRIES_ON_5XX})")
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
    """Macht eine Message-Liste kompatibel zu einem Request OHNE tools.

    - assistant-Messages mit `tool_calls` werden in reinen Text umgewandelt
      (als Zusammenfassung was Chanti gemacht hat).
    - role=tool Messages werden in user-Messages mit Präfix umgewandelt,
      damit das Modell sie noch als Kontext sieht aber keine tool_call_id
      mehr gematcht werden muss.
    """
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            # Tool-Calls in Text zusammenfassen, Content falls da anhängen.
            calls = msg.get("tool_calls") or []
            names = [tc.get("function", {}).get("name", "?") for tc in calls]
            summary = f"[Intern: Tools aufgerufen: {', '.join(names)}]"
            existing = (msg.get("content") or "").strip()
            text = (existing + "\n" + summary).strip() if existing else summary
            out.append({"role": "assistant", "content": text})
        elif role == "tool":
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            out.append({
                "role": "user",
                "content": f"[Ergebnis von {name}]: {content}",
            })
        else:
            # system / user / normale assistant-Messages unverändert
            out.append(msg)
    return out


def chat(messages: list[dict], tools: list[dict] = None,
         executors: dict = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    local_messages = list(messages)

    # Modell-Auswahl: Tool-fähiges Modell nur wenn Tools übergeben
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

    logger.info(f"Modell: {model}")

    # Zähler für Wiederholungen derselben Tool-Signatur.
    repeat_counts: dict[str, int] = {}
    # Zähler für „selbes Tool mehrfach in Folge" (fuzzy Loop).
    last_tool_name: str | None = None
    consecutive_same_tool: int = 0

    for round_num in range(MAX_TOOL_ROUNDS):
        resp = _request_with_retries(payload)
        if resp is None:
            return "Entschuldigung Kevin, ich kann Groq gerade nicht erreichen."

        if not resp.ok:
            error_data = {}
            try:
                error_data = resp.json().get("error", {}) or {}
            except ValueError:
                pass
            failed_gen = error_data.get("failed_generation", "")
            logger.warning(f"Groq Fehler {resp.status_code}: "
                           f"{error_data.get('message', '')[:200]}")

            if failed_gen and executors:
                tool_result = _parse_failed_generation(failed_gen, executors)
                if tool_result:
                    logger.info("Fallback erfolgreich, sende Ergebnis zurück")
                    fallback_resp = _request_with_retries({
                        "model": GROQ_MODEL,
                        "messages": list(local_messages) + [{
                            "role": "user",
                            "content": (f"[Tool-Ergebnis]: {tool_result}\n\n"
                                        "Bitte antworte basierend auf diesem Ergebnis."),
                        }],
                        "temperature": 0.7,
                        "max_tokens": max_tokens,
                    })
                    if fallback_resp is not None and fallback_resp.ok:
                        return _extract_content(fallback_resp.json())

            # Fallback: Nochmal ohne Tools versuchen — aber mit dem AKTUELLEN
            # Stand der Konversation (inkl. bereits erfolgter Tool-Calls und
            # deren Ergebnisse). Sonst gehen erfolgreiche Schritte dieses Turns
            # verloren, z.B. wenn str_replace schon gelaufen ist und dann erst
            # die Antwort-Generierung hakt.
            logger.info("Versuche ohne Tools…")
            sanitized = _sanitize_for_no_tools(local_messages)
            fallback_resp = _request_with_retries({
                "model": GROQ_MODEL,
                "messages": sanitized,
                "temperature": 0.7,
                "max_tokens": max_tokens,
            })
            if fallback_resp is not None and fallback_resp.ok:
                return _extract_content(fallback_resp.json())

            return "Entschuldigung Kevin, da ist etwas schiefgelaufen. Versuch es nochmal."

        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError) as e:
            logger.error(f"Ungültige Groq-Antwort: {e}")
            return "Entschuldigung Kevin, die Antwort von Groq war unvollständig."

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
                logger.warning(f"Tool-Args nicht parsebar für {fn_name}: {e}")

            logger.info(f"Tool-Call #{round_num+1}: {fn_name}({_redact(fn_args)})")

            # Fuzzy Loop-Detection: selbes Tool mehrfach in Folge.
            if fn_name == last_tool_name:
                consecutive_same_tool += 1
            else:
                consecutive_same_tool = 1
                last_tool_name = fn_name
            if consecutive_same_tool >= LOOP_DETECTION_SAME_TOOL:
                logger.warning(
                    f"Loop erkannt: {fn_name} {consecutive_same_tool}x in Folge "
                    f"— breche ab"
                )
                local_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn_name,
                    "content": (
                        f"Tool-Aufruf abgebrochen: du hast {fn_name} "
                        f"{consecutive_same_tool} mal in Folge aufgerufen. "
                        f"Wahrscheinlich ist der Ansatz falsch. Probiere ein "
                        f"anderes Tool oder sag dem User was nicht geht."
                    ),
                })
                return _final_answer_without_tools(local_messages, max_tokens)

            # Exakte Loop-Detection: wird dieselbe (name, args)-Kombi wiederholt?
            sig = _tool_signature(fn_name, fn_args) if args_ok else None
            if sig is not None:
                repeat_counts[sig] = repeat_counts.get(sig, 0) + 1
                if repeat_counts[sig] >= LOOP_DETECTION_REPEATS:
                    logger.warning(
                        f"Loop erkannt: {fn_name} mit identischen Args "
                        f"{repeat_counts[sig]}x aufgerufen — breche ab"
                    )
                    # Tool-Result künstlich einfügen, damit die tool_call_id
                    # bedient ist und das Modell eine saubere Abschluss-Runde
                    # ohne Tools bekommt.
                    local_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": fn_name,
                        "content": (
                            f"Tool-Aufruf abgebrochen: du hast {fn_name} "
                            f"bereits {repeat_counts[sig]} mal mit identischen "
                            f"Argumenten aufgerufen. Der Ansatz funktioniert "
                            f"offensichtlich nicht. Versuche etwas anderes "
                            f"oder sag dem User direkt, dass es nicht klappt."
                        ),
                    })
                    return _final_answer_without_tools(local_messages, max_tokens)

            if not args_ok:
                # Dem Modell klar sagen, dass die Args kaputt waren — dann
                # generiert es in der nächsten Runde hoffentlich bessere.
                result = (f"Tool-Aufruf abgelehnt: Argumente für {fn_name} "
                          f"waren nicht als JSON parsebar. Bitte erneut mit "
                          f"gültigen JSON-Argumenten aufrufen.")
            elif executors and fn_name in executors:
                try:
                    result = executors[fn_name](**fn_args)
                except TypeError as e:
                    # z.B. falsche/fehlende Parameter
                    logger.warning(f"Tool {fn_name} TypeError: {e}")
                    result = f"Tool-Fehler: ungültige Parameter ({e})."
                except Exception as e:
                    logger.error(f"Tool {fn_name} Exception: "
                                 f"{type(e).__name__}: {e}", exc_info=True)
                    result = (f"Tool-Fehler: {fn_name} hat einen internen "
                              f"Fehler ({type(e).__name__}). "
                              f"Sag Kevin bitte direkt, dass das Tool fehlgeschlagen ist.")
            else:
                result = f"Tool '{fn_name}' nicht verfügbar."

            logger.debug(f"Ergebnis: {str(result)[:300]}")

            local_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": fn_name,
                "content": str(result),
            })

    logger.warning(f"MAX_TOOL_ROUNDS ({MAX_TOOL_ROUNDS}) erreicht")
    return _final_answer_without_tools(local_messages, max_tokens)


def _final_answer_without_tools(messages: list[dict], max_tokens: int) -> str:
    """Gibt dem Modell eine letzte Chance, einen Abschluss-Satz zu formulieren
    — ohne Tools, damit kein weiterer Tool-Call kommt. Wird aufgerufen wenn
    der Loop abbricht (MAX_ROUNDS oder Loop-Detection)."""
    hint = {
        "role": "user",
        "content": (
            "Das Tool-Budget ist aufgebraucht. Fasse in 1–3 Sätzen zusammen "
            "was du versucht hast und was davon hat/nicht geklappt. "
            "Rufe KEIN weiteres Tool mehr auf."
        ),
    }
    sanitized = _sanitize_for_no_tools(messages)
    resp = _request_with_retries({
        "model": GROQ_MODEL,  # Nicht-Tool-Modell, damit tool_calls unwahrscheinlich
        "messages": sanitized + [hint],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    })
    if resp is not None and resp.ok:
        content = _extract_content(resp.json())
        if content:
            return content
    return "Ich konnte die Anfrage nicht abschließen — zu viele Tool-Runden."


def raw_chat(prompt: str) -> str:
    """Einfacher Chat ohne Tools. Mit Error-Handling und Retry."""
    resp = _request_with_retries({
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": RAW_MAX_TOKENS,
    })
    if resp is None or not resp.ok:
        if resp is not None:
            logger.error(f"raw_chat Fehler: {resp.status_code}")
        return ""
    try:
        return _extract_content(resp.json())
    except Exception as e:
        logger.error(f"raw_chat Parse-Fehler: {e}")
        return ""
