from pathlib import Path
from config import MEMORY_FILE
import re

def load() -> str:
    p = Path(MEMORY_FILE)
    if not p.exists():
        return "Noch keine Fakten gespeichert."
    content = p.read_text(encoding="utf-8").strip()
    return content if content else "Noch keine Fakten gespeichert."

def append(fact: str):
    p = Path(MEMORY_FILE)
    facts = []
    if p.exists():
        facts = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Duplikat-Check
    if fact not in facts:
        facts.append(fact)
    # Max 30 Fakten
    facts = facts[-30:]
    p.write_text("\n".join(facts) + "\n", encoding="utf-8")

def maybe_save(user_text: str, assistant_text: str) -> bool:
    # Explizit: "merke dir dass..."
    m = re.search(r"merke dir[,:]? (?:dass )?(.+)", user_text, re.I)
    if m:
        append(f"- {m.group(1).strip()}")
        return True
    # Chanti sagt selbst "ich merke mir..."
    m = re.search(r"ich merke mir[,:]? (.+?)[\.\!]", assistant_text, re.I)
    if m:
        append(f"- {m.group(1).strip()}")
        return True
    return False
