# TOOLS – Verfügbare Aktionen

## Home Assistant
- Lampen steuern: "[Lampenname] an/aus/[Farbe]/[Prozent]%"
- Beispiel: "ringlampe lila", "nachttischlampen aus", "alle lampen 50 prozent"

## Browser
- "öffne [URL oder Stichwort]" – öffnet http(s)-URL im Standard-Browser

## Websuche
- "suche nach [Begriff]" / "google [Begriff]"
- "lies [URL]" – Inhalt einer öffentlichen Webseite zusammenfassen

## Blender (nur wenn Blender läuft + MCP-Addon aktiv)
Strukturierte Aktionen, kein freies Python.

### Objekte erstellen
- "erstelle einen Würfel / eine Kugel / einen Zylinder / einen Kegel / eine Ebene / einen Torus / einen Affen"
- Optional: Name, Position, Größe, Farbe
- Beispiel: "baue mir einen roten Würfel bei 2,0,0 mit Größe 3"

### Objekte verändern
- "lösche [Objektname]"
- "bewege [Objektname] nach x,y,z" / "drehe [Objektname] um X Grad" / "skaliere [Objektname]"
- "mach [Objektname] blau" / "färbe [Objektname] in #ff8800"

### Szene
- "leere die Szene" / "lösche alles"
- "zeig mir die Szene" / "was ist in der Szene"

### Licht und Kamera
- "setze ein POINT-Licht bei 0,0,5" (Typen: POINT, SUN, SPOT, AREA)
- "setze die Kamera auf 7,-7,5 und schaue auf 0,0,0"

### Unterstützte Farben
rot, grün, blau, gelb, lila, pink, orange, cyan, weiß, schwarz, grau, braun – oder hex (`#rrggbb`)

## Kalender
- "erinnere mich an [Titel] am [Datum] um [Uhrzeit]"
- "zeig mir meine Termine"
- Reminder kommen als Telegram-Nachricht

## Leads
UI: http://localhost:8000/leads
- Branche + Ort + Radius → Firmen finden, analysieren, E-Mail-Entwürfe schreiben

## Eigene Dateien bearbeiten
Chanti darf nur folgende Dateien lesen und schreiben (innerhalb `~/chanti/`):
- SOUL.md – Persönlichkeit
- USER.md – Fakten über Kevin
- MEMORY.md – wichtige Ereignisse
- IDENTITY.md – Fähigkeiten
- TOOLS.md – diese Datei
- chat.html – Web-Chat UI (Layout, Styles, Farben)
- skills/*.py – eigene Skills

Einschränkungen: keine Symlinks, keine absoluten Pfade, keine `..`-Pfade, max 2 MB pro Write. Jede überschriebene Datei bekommt ein `.bak`.

WICHTIG: server.py, llm.py, memory.py und andere Python-Dateien
außer Skills NIEMALS bearbeiten – nur lesen wenn nötig.

## Selbst-Befehle (intern, nicht aussprechen)
- [MERKE: Fakt] → speichert Fakt in USER.md
- [KORRIGIERE: alt → neu] → aktualisiert Fakt in USER.md
- [EREIGNIS: Beschreibung] → speichert in MEMORY.md
