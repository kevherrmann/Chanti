import threading
from llm import raw_chat
from memory import append

def extract_and_save(user_text: str, assistant_text: str):
    def _run():
        prompt = f"""Nutzer sagte: "{user_text}"
Chanti antwortete: "{assistant_text}"

Gibt es hier einen dauerhaften wichtigen Fakt über Kevin (Projekte, Beruf, Vorlieben, Personen, Pläne)?
Antworte NUR mit dem Fakt als kurze Zeile, z.B. "- Kevin ist Programmierer" oder antworte "NEIN"."""

        result = raw_chat(prompt).strip()
        if result and result.upper() != "NEIN" and len(result) < 100:
            append(result)

    threading.Thread(target=_run, daemon=True).start()
