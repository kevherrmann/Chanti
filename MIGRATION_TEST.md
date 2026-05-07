# Chanti Mob-Agent Migration — Test-Anleitung

Stand: 2026-04-29
Phase: 3 — Test-Vorbereitung

## Ziel des Tests

Chanti bleibt technisch ein Mob/Lua-Entity, soll aber jetzt wie ein einfacher Spieler-Agent handeln können:

- laufen
- drehen
- springen
- Block vor sich abbauen
- Block aus Inventar vor sich platzieren
- Inventar prüfen
- aus Versuch/Ergebnis lernen

Der Test prüft nicht, ob sie schon intelligent baut. Der Test prüft zuerst, ob der neue Körper + Executor + Brain-Loop stabil laufen.

## Vorab: Was auf Hostinger passiert ist

☁️ Hostinger:

- Python-Syntaxcheck für `game_tools.py` und `game_brain.py`: OK.
- Lua-Syntaxcheck per `luac`: übersprungen, weil `luac` auf Hostinger nicht installiert ist.
- Service-Restart wurde vorbereitet/versucht, aber in dieser Sitzung nicht ausgeführt. Vor deinem lokalen Test muss der Chanti-Service neu gestartet werden, damit `game_brain.py` und `game_tools.py` aktiv werden.

Bitte auf Hostinger ausführen, falls ich es nicht nochmal mit Freigabe machen soll:

```bash
cd /workspace/chanti
sudo systemctl restart chanti
sudo journalctl -u chanti -n 60 --no-pager
```

Erwartung im Log:

- Keine Python-Tracebacks.
- Keine Importfehler zu `game_tools` oder `game_brain`.
- Chanti-Server startet normal.

Wenn ein Traceback erscheint: Test abbrechen und Log an Hermes geben.

## Vorab: Lokale Welt sichern

🖥️ Kevins PC:

Luanti schließen, dann Weltordner sichern.

Flatpak-Pfad wahrscheinlich:

```bash
cd ~/.var/app/org.luanti.luanti/.minetest/worlds
cp -a <DEINE_WELT> <DEINE_WELT>.bak-mob-agent-$(date +%Y%m%d-%H%M%S)
```

Falls der Pfad nicht existiert, suche nach deinen Welten:

```bash
find ~/.var/app/org.luanti.luanti -maxdepth 5 -type d -name worlds
find ~/.minetest ~/.luanti -maxdepth 3 -type d -name worlds 2>/dev/null
```

## Mod vom Server holen

🖥️ Kevins PC:

```bash
chanti-pull
```

Danach sollten lokal in deinem Luanti-Modordner diese Dateien aktualisiert sein:

- `avatar.lua`
- `executor.lua`
- `init.lua`
- `perception.lua`

## Welt starten

🖥️ Kevins PC:

Empfohlener erster Test: normale Welt/Host-Server starten, wie du es bisher gemacht hast.

Wichtig in der Weltkonfiguration:

- Mod `chanti_welt` aktiv.
- HTTP-Zugriff erlaubt:

```conf
secure.http_mods = chanti_welt
```

Falls du über Flatpak startest und Servermodus testen willst, kannst du später probieren:

```bash
flatpak run org.luanti.luanti --server --worldname <DEINE_WELT>
```

Für den ersten Test reicht aber die bekannte Startweise.

## Was du beim ersten Join sehen solltest

🖥️ In Luanti:

Nach dem Join sollte im Chat ungefähr stehen:

```text
[chanti_welt] Chanti ist als lernfähiger Mob-Agent aktiv. Bridge aktiv.
Tipp: /zuchanti -> teleportiert dich zu ihr. /chantipos -> zeigt Position. /chantiinv -> Inventar.
K-Taste = Schnell-Modus. J = Fliegen. H = Noclip.
```

Chanti sollte in deiner Nähe als hellblauer Mob spawnen.

## Manuelle Chatcommand-Checks

🖥️ In Luanti-Chat:

```text
/chantipos
```

Erwartung:

```text
Chanti ist bei (...) (Modus: mob_agent)
```

Dann:

```text
/chantiinv
```

Erwartung am Anfang wahrscheinlich:

```text
Chantis Inventar: {}
```

Dann:

```text
/zuchanti
```

Erwartung:

- Du wirst in Chantis Nähe teleportiert.

## Automatischer Brain-Test

Wenn Hostinger-Service neu gestartet ist und die Bridge funktioniert:

1. Warte 10-30 Sekunden.
2. Beobachte Chanti.
3. Sie sollte anfangen, kleine Pläne auszuführen.

Mögliche Aktionen:

- drehen
- laufen
- warten
- schauen
- springen
- gelegentlich `dig_forward`
- später `place_forward`, wenn sie Items im Inventar hat

Wichtig: Am Anfang darf sie unbeholfen wirken. Das ist gewollt. Sie soll erst lernen, was Aktionen bewirken.

## Was im Erfolgsfall auf Hostinger entstehen sollte

☁️ Hostinger:

Nach einigen Aktionen sollte diese Datei entstehen:

```bash
/workspace/chanti/data/world_learning.jsonl
```

Prüfen:

```bash
tail -20 /workspace/chanti/data/world_learning.jsonl
```

Erwartete Einträge sehen ungefähr so aus:

```json
{"t":...,"plan_id":"brain-...","action":"move_forward","args":{"steps":1},"success":true,"reason":"angekommen"}
{"t":...,"plan_id":"brain-...","action":"dig_forward","args":{},"success":true,"reason":"abgebaut default:dirt -> default:dirt"}
```

Das ist Chantis technisches Lernprotokoll, nicht ihr privates Tagebuch.

## Logs prüfen

### Luanti lokal

🖥️ Je nach Installation ist `debug.txt` ungefähr hier:

```bash
~/.var/app/org.luanti.luanti/.minetest/debug.txt
```

oder:

```bash
~/.minetest/debug.txt
~/.luanti/debug.txt
```

Suche nach `chanti_welt`:

```bash
grep -i "chanti_welt\|error\|traceback" ~/.var/app/org.luanti.luanti/.minetest/debug.txt | tail -80
```

Falls der Pfad anders ist, `debug.txt` suchen:

```bash
find ~/.var/app/org.luanti.luanti ~/.minetest ~/.luanti -name debug.txt 2>/dev/null
```

### Hostinger

☁️ Hostinger:

```bash
sudo journalctl -u chanti -n 100 --no-pager
```

Achte auf:

- `HTTP-Bridge: Session gestartet`
- `Brain: Plan gesendet`
- `Brain: plan_result`
- keine Tracebacks
- keine 403 Token-Fehler

## Bekannte erwartete Probleme

### 1. Chanti baut eventuell zu viel Boden ab

`dig_forward` baut den Block direkt vor ihren Füßen ab. Das ist erstmal ein primitives Experimentierwerkzeug. Später sollten wir Sicherheitsregeln ergänzen:

- nicht direkt unter sich abbauen
- nicht in Lava/Wasser reinlaufen
- Bau-/Abbau-Zonen begrenzen

### 2. Platzieren geht erst, wenn Inventar Items enthält

`place_forward` scheitert erwartbar mit:

```text
nicht im Inventar: <item>
```

wenn Chanti noch nichts abgebaut hat.

### 3. Drop-Logik ist simpel

Der erste Stand nutzt einfache Drop-Ermittlung. Komplexe Drops/Crafting/Tools sind noch nicht vollständig simuliert.

### 4. Mob sieht noch nicht wie echter Player aus

Diese Phase fokussiert Fähigkeiten und Lernen. Player-Mesh/Animation können danach separat schöner gemacht werden.

### 5. Lua-Syntax wurde noch nicht mit `luac` geprüft

Auf Hostinger fehlt `luac`. Der echte Syntax-Test passiert beim Luanti-Start. Falls Luanti direkt beim Laden der Mod einen Lua-Fehler zeigt, Log an Hermes geben.

### 6. Hostinger-Service muss neu gestartet sein

Ohne Neustart kennt das Brain die neuen Aktionen nicht. Dann kann es passieren, dass Lua neue Aktionen könnte, aber der Server sie nie plant.

## Abbruchkriterien

Bitte Test abbrechen und Logs schicken, wenn:

- Luanti-Welt wegen Lua-Fehler nicht startet.
- Chanti nicht spawnt.
- `/chantipos` Fehler ausgibt.
- Hostinger-Log Python-Tracebacks zeigt.
- Brain sendet gar keine Pläne trotz aktiver Welt.
- Chanti führt eine Aktion endlos aus oder Poll-/State-Logs explodieren.

## Rollback

### Lokale Mod zurückrollen

☁️/🖥️ Wenn du die `.bak`-Dateien aus der Serverkopie nutzen willst:

```bash
cp /workspace/chanti-game-mod/avatar.lua.bak /workspace/chanti-game-mod/avatar.lua
cp /workspace/chanti-game-mod/executor.lua.bak /workspace/chanti-game-mod/executor.lua
cp /workspace/chanti-game-mod/init.lua.bak /workspace/chanti-game-mod/init.lua
cp /workspace/chanti-game-mod/perception.lua.bak /workspace/chanti-game-mod/perception.lua
```

Dann auf deinem PC wieder:

```bash
chanti-pull
```

### Hostinger-Python zurückrollen

☁️ Hostinger:

```bash
cp /workspace/chanti/game_tools.py.bak /workspace/chanti/game_tools.py
cp /workspace/chanti/game_brain.py.bak /workspace/chanti/game_brain.py
sudo systemctl restart chanti
sudo journalctl -u chanti -n 60 --no-pager
```

### Welt zurückrollen

🖥️ Kevins PC:

```bash
# Luanti vorher schließen
cd ~/.var/app/org.luanti.luanti/.minetest/worlds
mv <DEINE_WELT> <DEINE_WELT>.failed-mob-agent-$(date +%Y%m%d-%H%M%S)
mv <DEINE_WELT>.bak-mob-agent-<TIMESTAMP> <DEINE_WELT>
```

## Was ich von dir nach dem Test brauche

Bitte schick mir:

1. Ob Chanti gespawnt ist.
2. Ausgabe von `/chantipos`.
3. Ausgabe von `/chantiinv` nach ein paar Minuten.
4. Relevante Zeilen aus Luanti `debug.txt` mit `chanti_welt`.
5. Relevante Hostinger-Logs mit `Brain:` und `HTTP-Bridge:`.
6. Falls vorhanden: letzte Zeilen aus `data/world_learning.jsonl`.
