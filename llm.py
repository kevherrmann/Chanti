"""LLM via Groq mit Tool-Calling Support."""
import requests
import json
import re
import logging
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_MODEL_TOOLS

logger = logging.getLogger("chanti")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Maximale Tool-Call-Runden pro Anfrage
MAX_TOOL_ROUNDS = 8


def _parse_failed_generation(failed_gen: str, executors: dict) -> str | None:
    """Fallback falls Modell Tool-Call im falschen Format generiert."""
    m = re.search(r'<function=(\w+)\s*(\{.*?\})(?:</function>|>)', failed_gen, re.DOTALL)
    if not m:
        m = re.search(r'<function=(\w+)\s*(\{.*)', failed_gen, re.DOTALL)
    if not m:
        return None

    fn_name = m.group(1)
    args_str = m.group(2).strip().rstrip('>')
    try:
        fn_args = json.loads(args_str)
    except Exception:
        return None

    logger.info(f"Fallback Tool-Call: {fn_name}({fn_args})")
    if fn_name in executors:
        try:
            return str(executors[fn_name](**fn_args))
        except Exception as e:
            return f"Fehler: {e}"
    return None


def _groq_request(payload: dict, timeout: int = 30) -> requests.Response:
    """Zentraler Groq-API-Call mit Error-Handling."""
    return requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=timeout
    )


def chat(messages: list[dict], tools: list[dict] = None, executors: dict = None) -> str:
    local_messages = list(messages)

    # Modell-Auswahl: Tool-fähiges Modell nur wenn Tools übergeben
    model = GROQ_MODEL_TOOLS if tools else GROQ_MODEL

    payload = {
        "model": model,
        "messages": local_messages,
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = False

    logger.info(f"Modell: {model}")

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            resp = _groq_request(payload)
        except requests.exceptions.Timeout:
            logger.error("Groq Timeout")
            return "Entschuldigung Kevin, die Anfrage hat zu lange gedauert."
        except requests.exceptions.ConnectionError:
            logger.error("Groq nicht erreichbar")
            return "Entschuldigung Kevin, ich kann Groq gerade nicht erreichen."

        if not resp.ok:
            error_data = resp.json().get("error", {})
            failed_gen = error_data.get("failed_generation", "")
            logger.warning(f"Groq Fehler {resp.status_code}: {error_data.get('message', '')}")

            if failed_gen and executors:
                tool_result = _parse_failed_generation(failed_gen, executors)
                if tool_result:
                    logger.info("Fallback erfolgreich, sende Ergebnis zurück")
                    try:
                        fallback_resp = _groq_request({
                            "model": GROQ_MODEL,
                            "messages": list(messages) + [{
                                "role": "user",
                                "content": f"[Tool-Ergebnis]: {tool_result}\n\nBitte antworte basierend auf diesem Ergebnis."
                            }],
                            "temperature": 0.7,
                            "max_tokens": 1024,
                        })
                        if fallback_resp.ok:
                            return fallback_resp.json()["choices"][0]["message"].get("content", "").strip()
                    except requests.exceptions.RequestException:
                        pass

            # Fallback: Nochmal ohne Tools versuchen
            logger.info("Versuche ohne Tools...")
            try:
                fallback_resp = _groq_request({
                    "model": GROQ_MODEL,
                    "messages": list(messages),
                    "temperature": 0.7,
                    "max_tokens": 1024,
                })
                if fallback_resp.ok:
                    return fallback_resp.json()["choices"][0]["message"].get("content", "").strip()
            except requests.exceptions.RequestException:
                pass

            return "Entschuldigung Kevin, da ist etwas schiefgelaufen. Versuch es nochmal."

        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "")

        if finish_reason != "tool_calls":
            return (message.get("content") or "").strip()

        tool_calls = message.get("tool_calls", [])

        assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
        if message.get("content"):
            assistant_msg["content"] = message["content"]
        local_messages.append(assistant_msg)
        payload["messages"] = local_messages

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except Exception:
                fn_args = {}

            logger.info(f"Tool-Call #{round_num+1}: {fn_name}({fn_args})")

            if executors and fn_name in executors:
                try:
                    result = executors[fn_name](**fn_args)
                except Exception as e:
                    result = f"Fehler bei {fn_name}: {e}"
            else:
                result = f"Tool '{fn_name}' nicht verfügbar."

            logger.debug(f"Ergebnis: {str(result)[:300]}")

            local_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": fn_name,
                "content": str(result)
            })

    return "Ich konnte die Anfrage nicht abschließen."


def raw_chat(prompt: str) -> str:
    """Einfacher Chat ohne Tools. Mit Error-Handling."""
    try:
        resp = _groq_request({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 50
        })
        if not resp.ok:
            logger.error(f"raw_chat Fehler: {resp.status_code}")
            return ""
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        logger.error(f"raw_chat Netzwerkfehler: {e}")
        return ""
