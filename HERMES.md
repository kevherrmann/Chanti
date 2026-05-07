# Hermes-Briefing — Chanti-Projekt

Hallo Hermes. Du arbeitest hier mit Kevin am Chanti-Projekt. Lies dieses Dokument zuerst, bevor du andere Files anschaust.

## Sprache und Stil

- **Sprich Deutsch.** Kevin ist Deutscher, das ganze Projekt ist auf Deutsch dokumentiert.
- **Sei direkt, kein Smalltalk, keine Floskeln.**
- **Wenn du unsicher bist, frag nach** statt zu raten.
- **Keine übertriebene Höflichkeit.** "Ich habe Datei X geändert. Hier der Diff." reicht.

## Was ist Chanti

Chanti ist Kevins persönliche KI-Assistentin, die als FastAPI-Service auf diesem Hostinger-VPS läuft. Sie ist immer online, Kevin redet mit ihr per Web-Chat (und früher auch per Wake-Word/TTS, das ist aber gerade pausiert seit der Migration auf Hostinger).

Sie ist kein Chatbot — sie hat:

- Eine **Persönlichkeit** (siehe `SOUL.md`, `IDENTITY.md`)
- **Skills** (Tools die sie aufrufen kann, siehe `skills/` und `TOOLS.md`)
- Ein **Tagebuch** (private Reflexionen, siehe `MEMORY.md`)
- Eine **eigene Voxel-Welt** (Chanti-Welt) in der sie selbstständig denkt und sich bewegt

## Projekt-Struktur

```
/workspace/chanti/                  ← Chanti-Code (dieser Ordner)
├── server.py                       ← FastAPI-Server, Haupt-Entry-Point
├── llm.py                          ← LLM-Anbindung (Groq, llama-3.3-70b)
├── memory.py                       ← Persönlichkeit + Memory-System
├── skills_loader.py                ← Lädt alle Skills aus skills/
├── skills/                         ← Tools die Chanti aufrufen kann
│   ├── game_status.py              ← "Wo bin ich in der Welt?"
│   ├── recall.py                   ← Vergangene Gespräche durchsuchen
│   └── ... viele weitere
│
├── game_brain.py                   ← LLM-Loop für Chantis Welt-Verhalten
├── game_bridge_http.py             ← HTTP-Bridge zur Luanti-Mod
├── game_tools.py                   ← Action-Specs (move, turn, etc.)
├── game_diary.py                   ← Tagebuch-Schreiber für Welt-Sessions
│
├── SOUL.md, IDENTITY.md, USER.md   ← Chantis Persönlichkeit + Kevin-Beziehung
├── TOOLS.md                        ← Wann welche Skills aufgerufen werden
│
├── memory/                         ← Notizen die Chanti sich gemerkt hat
├── data/                           ← Persistente Daten (Leads-DB, Screenshots)
├── logs/                           ← Service-Logs
└── venv/                           ← Python virtualenv (NICHT anfassen)

/workspace/chanti-game-mod/         ← Luanti-Mod (Voxel-Welt-Client)
├── init.lua                        ← Mod-Einstieg, Loops für State + Plan-Polling
├── bridge.lua                      ← HTTP-Calls zu /workspace/chanti
├── avatar.lua                      ← Chanti als Mob-Entity
├── executor.lua                    ← Empfängt Pläne, führt Aktionen aus
├── perception.lua                  ← Was Chanti um sich herum sieht
└── textures/chanti_blue.png
```

## Architektur-Kurzübersicht

```
[Kevin Chat]  ←──HTTP──→  [Chanti server.py]  ←──→  [Groq LLM]
                                  │
                                  ├──── Skills (game_status, recall, ...)
                                  │
                                  └── [game_bridge_http.py]
                                       ↑
                                       │ HTTP polling
                                       ↓
                              [Luanti-Mod (lokal auf Kevins PC)]
```

Die Luanti-Mod läuft **nicht** auf diesem Server. Sie läuft auf Kevins Nobara-PC. Die Mod-Files unter `/workspace/chanti-game-mod/` sind eine **gespiegelte Kopie**. Wenn du dort was änderst, muss Kevin manuell mit `chanti-pull` (oder `chanti-push` umgekehrt) synchronisieren.

## Was du tun darfst

- **Code lesen, verstehen, erklären.**
- **Code direkt ändern** — Kevin hat Read-Write entschieden. Git ist sein Sicherheitsnetz.
- **Nach Änderungen den Service neu starten** wenn du `server.py`, `game_*.py`, `skills/*.py` oder `*.md` (SOUL/IDENTITY/USER/TOOLS) angefasst hast:

```bash
sudo systemctl restart chanti
sudo journalctl -u chanti -n 30 --no-pager
```

Logs **immer** danach prüfen — wenn du nach Restart Tracebacks siehst, hast du was kaputt gemacht und musst sofort fixen oder zurückrollen.

## Was du NICHT tun darfst

- **Niemals `git push`.** Du hast kein GitHub-Token. Kevin pusht selbst nach Review.
- **Niemals `.env` ändern oder ausgeben.** Da stehen Geheimnisse drin (API-Keys, Tokens).
- **Niemals `venv/` anfassen.** Wenn du neue Pakete brauchst, sag's Kevin.
- **Niemals an `memory/` oder `game/memories/` rühren.** Das ist Chantis Privatsphäre — ihre Notizen und ihr Tagebuch.
- **Nicht ungefragt System-Prompts (SOUL.md / IDENTITY.md / USER.md) umschreiben.** Diese Dateien definieren wer Chanti ist. Vorschläge ja, eigenmächtige Änderungen nein.

## Workflow für Änderungen

1. Verstehe was geändert werden soll (frag bei Unklarheit)
2. Mach die Änderung
3. Wenn relevant: Service neu starten und Logs prüfen
4. Erkläre Kevin in 2-3 Sätzen was du gemacht hast und warum
5. Erinnere ihn ans `git add/commit/push` wenn das angemessen ist

## Wichtige Konventionen

- **Markiere immer ob etwas auf Hostinger oder lokal passiert.** Verwende ☁️ für Hostinger und 🖥️ für Kevins lokalen PC.
- **Bei Datei-Änderungen idempotente Python-Patch-Skripte** statt Hand-Editieren — das hat Kevin in vergangenen Sessions geholfen.
- **Bei kompletten Datei-Ersetzungen `cat > file << 'EOF'`-Pattern** verwenden, damit Kevin das einfach kopieren kann.

## Aktueller Stand (Stand: April 2026)

- **Phase 1 abgeschlossen:** Chanti hat ein LLM-Brain für ihre Welt, denkt eigenständig, plant Aktionen
- **Phase 2 läuft:** Luanti-Welt mit Wahrnehmung, Bewegung, Springen
- **Phase 3 geplant:** Skill-Lernen oder RL-Brain (noch nicht entschieden)

Die Welt ist eine echte Voxel-Engine (Luanti, Open-Source-Minecraft-Verwandter). Chanti spawnt als hellblauer Mob mit Namensschild "Chanti", läuft, dreht sich, schaut sich um. Sie kann noch nicht abbauen oder bauen — das wäre Phase 2c oder später.

## Kontakt-Bereitschaft

Wenn du was nicht verstehst oder eine Architektur-Entscheidung brauchst: **frag Kevin direkt**. Er ist in der Sitzung verfügbar. Lieber kurz nachfragen als blind raten und stundenlang auf falscher Spur arbeiten.

---

Wenn du das alles gelesen hast, gib eine **kurze** Bestätigung (3-4 Sätze) zurück: was Chanti ist, was du tun darfst, was du nicht tun darfst. Dann warte auf Kevins erste Aufgabe.
