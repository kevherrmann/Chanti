"""LLM via Groq mit Tool-Calling Support."""
import requests
import json
import re
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_MODEL_TOOLS

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


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

    print(f"[Chanti] Fallback Tool-Call: {fn_name}({fn_args})")
    if fn_name in executors:
        try:
            return str(executors[fn_name](**fn_args))
        except Exception as e:
            return f"Fehler: {e}"
    return None


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

    print(f"[Chanti] Modell: {model}")

    for _ in range(8):
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )

        if not resp.ok:
            error_data = resp.json().get("error", {})
            failed_gen = error_data.get("failed_generation", "")
            print(f"[Chanti] Groq Fehler {resp.status_code}: {error_data.get('message', '')}")

            if failed_gen and executors:
                tool_result = _parse_failed_generation(failed_gen, executors)
                if tool_result:
                    print(f"[Chanti] Fallback erfolgreich, sende Ergebnis zurück")
                    fallback_resp = requests.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": GROQ_MODEL,
                            "messages": list(messages) + [{
                                "role": "user",
                                "content": f"[Tool-Ergebnis]: {tool_result}\n\nBitte antworte basierend auf diesem Ergebnis."
                            }],
                            "temperature": 0.7,
                            "max_tokens": 1024,
                        },
                        timeout=30
                    )
                    if fallback_resp.ok:
                        return fallback_resp.json()["choices"][0]["message"].get("content", "").strip()

            # Fallback: Nochmal ohne Tools versuchen
            print(f"[Chanti] Versuche ohne Tools...")
            fallback_resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": list(messages),
                    "temperature": 0.7,
                    "max_tokens": 1024,
                },
                timeout=30
            )
            if fallback_resp.ok:
                return fallback_resp.json()["choices"][0]["message"].get("content", "").strip()

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

            print(f"[Chanti] Tool-Call: {fn_name}({fn_args})")

            if executors and fn_name in executors:
                try:
                    result = executors[fn_name](**fn_args)
                except Exception as e:
                    result = f"Fehler bei {fn_name}: {e}"
            else:
                result = f"Tool '{fn_name}' nicht verfügbar."

            print(f"[Chanti] Ergebnis: {str(result)[:300]}")

            local_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": fn_name,
                "content": str(result)
            })

    return "Ich konnte die Anfrage nicht abschließen."


def raw_chat(prompt: str) -> str:
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 50
        },
        timeout=30
    )
    return resp.json()["choices"][0]["message"]["content"].strip()
