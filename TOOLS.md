# TOOLS – Verfügbare Aktionen

## Home Assistant
- Lampen steuern: "[Lampenname] an/aus/[Farbe]/[Prozent]%"
- Beispiel: "ringlampe lila", "nachttischlampen aus", "alle lampen 50 prozent"

## Browser
- "öffne [URL oder Stichwort]"

## Websuche
- "suche nach [Begriff]" / "google [Begriff]"

## Blender (nur wenn aktiv)
- "würfel erstellen", "kugel erstellen"
- "szene abfragen", "alles löschen"

## Eigene Dateien bearbeiten
Chanti darf nur folgende Dateien lesen und schreiben:
- SOUL.md – Persönlichkeit
- USER.md – Fakten über Kevin
- MEMORY.md – wichtige Ereignisse
- IDENTITY.md – Fähigkeiten
- TOOLS.md – diese Datei
- chat.html – Web-Chat UI (Layout, Styles, Farben)
- skills/*.py – eigene Skills

WICHTIG: server.py, llm.py, memory.py und andere Python-Dateien
außer Skills NIEMALS bearbeiten – nur lesen wenn nötig.

## Selbst-Befehle (intern, nicht aussprechen)
- [MERKE: Fakt] → speichert Fakt in USER.md
- [KORRIGIERE: alt → neu] → aktualisiert Fakt in USER.md
- [EREIGNIS: Beschreibung] → speichert in MEMORY.md

## Lead-Generator
- "Such mir X [Branche] in [Ort]"
- "Finde potenzielle Kunden im Bereich [Branche] in [Ort]"
- Ergebnisse werden als JSON in leads/ gespeichert
