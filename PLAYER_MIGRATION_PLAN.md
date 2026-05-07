# Chanti Player-Migration — Recherche und Plan

Stand: 2026-04-29
Phase: 1 — Recherche und Migrations-Plan

## Kurzfazit

Update nach Kevins Entscheidung: Wir migrieren nicht zu einem echten Player. Chanti bleibt technisch ein Mob/Lua-Entity, wird aber als "spielerähnlicher lernfähiger Agent" ausgebaut.

Begründung: Die gewünschte Migration "Mob -> echter synthetischer Player" geht nicht rein serverseitig mit der Luanti-Lua-API. Luanti stellt in Lua ObjectRefs für bereits verbundene Player bereit (`core.get_player_by_name`, `core.get_connected_players`) und kann Lua-Entities erzeugen (`core.add_entity`). Ich habe aber keine API gefunden, mit der ein Mod synthetische/Bot-Player erzeugt oder Player-Eingaben serverseitig setzt.

Neues Ziel:

1. Chanti bleibt eine kontrollierbare Lua-Entity.
2. Sie bekommt primitive Spielerfähigkeiten: laufen, drehen, springen, abbauen, platzieren, Inventar prüfen.
3. Sie bekommt Wahrnehmung + Versuch/Ergebnis-Rückmeldung.
4. Aus jedem Plan-Ergebnis entsteht ein technisches Lernprotokoll unter `data/world_learning.jsonl`.
5. Später können aus diesen Beobachtungen höhere Skills entstehen: Material sammeln, einfache Formen bauen, Gebäude planen.

## Quellen und Recherche-Ergebnisse

### Luanti API

Quellen:
- https://api.luanti.org/
- https://api.luanti.org/core-namespace-reference/
- https://api.luanti.org/class-reference/
- https://docs.luanti.org/for-server-hosts/setup/
- https://docs.luanti.org/for-server-hosts/setup/linux/
- https://flathub.org/apps/org.luanti.luanti

Relevante API-Befunde:

- Mods laufen serverseitig. Die API-Doku beschreibt Mods als serverseitige Lua-Skripte; Media/Definitionen werden an Clients übertragen.
- `core.add_entity(pos, name, [staticdata])` erzeugt Lua-Entities, keine Player.
- `core.get_player_by_name(name)` gibt ein ObjectRef für einen Player zurück, aber nur wenn dieser Player existiert/online ist.
- `core.get_connected_players()` gibt ObjectRefs verbundener Player zurück.
- `core.is_player(obj)` prüft, ob ein ObjectRef ein Player ist.
- Player-spezifische Methoden existieren (`get_player_name`, `get_look_dir`, `get_look_horizontal`, `set_look_horizontal`, `set_physics_override`, `get_player_control`, `get_player_control_bits`).
- `get_player_control()` ist read-only: Es liest echte Client-Eingaben. Es gibt kein entsprechendes `set_player_control()` in der API.
- `set_velocity()` ist laut Class-Reference Lua-entity-only. Für Player wird stattdessen `add_velocity()` unterstützt; damit kann man Impulse geben, aber keine kontinuierliche normale Player-Steuerung simulieren.
- `move_to()` ist für Player praktisch `set_pos`; `continuous` wird bei Playern ignoriert. Das wäre Teleport-/Snap-Bewegung, keine echte Walk-Physik.

Bewertung:

- Native synthetische/Bot-Player per Lua: nicht gefunden.
- Server kann echte Online-Player manipulieren, aber nicht wie ein Client "laufen lassen".
- Für echte Player-Physik braucht Chanti einen echten verbundenen Client, der Eingaben sendet, oder einen speziellen Bot-Client außerhalb der Mod.

### Bestehende Bot-/NPC-Mods

Quellen:
- ContentDB Suche nach "bot": https://content.luanti.org/packages/?q=bot
- PvP Training Bot Mod: https://content.luanti.org/packages/TigerMask1/practice_bot/
- ContentDB Suche nach "npc": https://content.luanti.org/packages/?q=npc

Ergebnis:

- Gefundene Bot-/NPC-Mods sind nach ContentDB-Beschreibung überwiegend Mobs/NPCs, nicht native Player.
- Beispiel `practice_bot`: Beschreibt "practice bots", hängt aber an Mobs/NPCs und optional `player_api`; es nutzt offenbar Player-ähnliche Darstellung/Animation, nicht echte synthetische Player-Accounts.
- `mobs_redo`, `mobs_npc`, `aliveai`, NPC-Talk-Mods etc. lösen das klassische NPC-Problem als Entity/Mob-Schicht.

Bewertung:

- Die Community-Lösung ist offenbar: Bots als Entities mit Player-Modell, nicht als echte Player.
- Das bestätigt die API-Grenze: Echte Player entstehen durch Client-Verbindungen, nicht durch Mods.

## Server-Modus: Was ändert sich für Kevin?

### Start als Server statt Singleplayer

🖥️ Lokal auf Kevins PC gibt es drei Varianten:

1. Einfachste Variante über GUI:
   - Luanti starten.
   - Bestehende Welt auswählen.
   - "Host Server" / Server hosten aktivieren.
   - Name/Passwort setzen.
   - Welt starten.
   - Danach als Beobachter in derselben Instanz spielen oder mit zweiter Instanz verbinden.

2. Flatpak CLI, wenn der Flatpak die Luanti-Binary durchreicht:

```bash
flatpak run org.luanti.luanti --server --worldname <WELTNAME>
```

Optional mit Game-ID, falls mehrere Games/Welten Probleme machen:

```bash
flatpak run org.luanti.luanti --server --worldname <WELTNAME> --gameid <GAMEID>
```

3. Dedizierter Server ohne GUI:
   - Laut Linux-Server-Doku ist `luantiserver` empfohlen; bei Nicht-Headless-Systemen funktioniert auch `luanti --server` ähnlich.
   - Bei Flatpak ist wahrscheinlich Variante 1 oder 2 praktikabler als ein separater `luantiserver`.

Wichtig: Flatpak speichert User-Daten in der Flatpak-Sandbox. Der genaue Pfad muss auf Kevins PC geprüft werden. Typisch ist unter Flatpak etwas wie:

```bash
~/.var/app/org.luanti.luanti/.minetest/
```

oder bei neuer Luanti-Benennung ggf. ein `.luanti`-Pfad. Die API-Doku sagt allgemein: Linux-Userpfad ist bei systemweiten Builds normalerweise `~/.minetest`; Flatpak kapselt das unter `~/.var/app/...`.

### Authentifizierung / Account-Files

- Multiplayer/Server-Modus nutzt Player-Namen und Auth-Daten.
- Die API hat Auth-Handler und Funktionen wie `core.set_player_password`, `core.set_player_privs`, `core.auth_reload`.
- Für Kevin als Beobachter reicht ein normaler Player-Account mit Privs, die der Mod aktuell schon auf Join setzt (`fly`, `fast`, `noclip`).
- Für echten Player-Ansatz muss Account `Chanti` existieren und online sein. Der Mod kann den Account nicht als Online-Player erzeugen; er kann ihn nur erkennen, wenn ein Client mit diesem Namen verbunden ist.

### Bestehende Welt

- Bestehende Welt sollte erhalten bleiben, wenn dieselbe Welt im Server-/Host-Server-Modus gestartet wird.
- Risiko ist nicht der Server-Modus selbst, sondern falscher Datenpfad/falsche Welt-Auswahl bei Flatpak oder eine Welt, die mit anderer Mod-Konfiguration gestartet wird.
- Vor jedem Test: Welt-Ordner sichern.

Empfohlene Sicherung auf Kevins PC:

```bash
# Pfad vorher prüfen; Beispiel für Flatpak-Sandbox
cd ~/.var/app/org.luanti.luanti/.minetest/worlds
cp -a <WELTNAME> <WELTNAME>.bak-player-migration-$(date +%Y%m%d-%H%M%S)
```

## Analyse des bestehenden Codes

Gelesene Dateien:

- `/workspace/chanti-game-mod/init.lua`
- `/workspace/chanti-game-mod/avatar.lua`
- `/workspace/chanti-game-mod/executor.lua`
- `/workspace/chanti-game-mod/perception.lua`
- `/workspace/chanti-game-mod/bridge.lua`
- `/workspace/chanti/game_tools.py`

Ist-Zustand:

- `avatar.lua` registriert `chanti_welt:chanti` als Lua-Entity mit Cube-Visual, Kollision und eigener Walk-Logik.
- `executor.lua` führt Plan-Aktionen aus. `move_forward` berechnet ein Ziel und ruft `avatar.walk_to(target, callback)`.
- `init.lua` spawnt beim Join eines beliebigen Players Chanti in dessen Nähe und startet State-/Poll-Loops.
- `perception.lua` arbeitet generisch mit ObjectRef (`get_pos`, `get_yaw`) und sollte mit Entity oder Player grundsätzlich funktionieren.
- `game_tools.py` auf Hostinger definiert dieselben Aktionen; dort ist für Phase 2 wahrscheinlich keine Änderung nötig, solange Aktionen gleich bleiben.

## Konkreter Migrations-Plan

### Entscheidungspunkt vor Phase 2

Kevin muss entscheiden, welche Zielvariante wir implementieren:

A. "Player-like Entity" — empfohlen für Phase 2:
   - Chanti bleibt serverseitige Entity.
   - Ziel ist sicht-/verhaltensmäßig näher an Player: Player-Mesh/Textur/Animation statt Cube, weniger fragile Spawnlogik, Beobachter als separater Player.
   - Kein externer Bot-Client nötig.
   - Bewegung bleibt vollständig kontrollierbar durch Server-Lua.

B. "Echter Online-Player Chanti" — nur mit externer Vorbedingung:
   - Chanti ist ein echter Account und muss per zweitem Client verbunden sein.
   - Mod referenziert `minetest.get_player_by_name("Chanti")`.
   - Bewegung wäre zunächst per `set_pos`/`move_to`/`add_velocity`, also nicht die gewünschte natürliche Player-Input-Physik.
   - Für echte WASD-Physik brauchen wir später einen externen Bot-Client, den diese Phase nicht abdeckt.

C. Hybrid/Fallback:
   - Wenn Player `Chanti` online ist, benutzt die Mod ihn.
   - Wenn nicht, spawnt sie den alten Mob bzw. die Player-like Entity.
   - Gute Testbarkeit und sicherer Rollback.

Mein Vorschlag für Phase 2: Variante C als vorsichtige Migration mit Default auf Entity-Fallback. Damit scheitert die Welt nicht, wenn der Chanti-Player nicht verbunden ist.

### Schritt-für-Schritt-Plan für Variante C

#### Schritt 1 — Git-Sicherung

☁️ Hostinger / Repo:

```bash
cd /workspace/chanti
git status --short
git tag pre-player-migration-20260429
```

Wichtig: Es gibt aktuell schon uncommitted/unknown Zustand (`D hello.py`, `?? HERMES.md`). Vor dem Tag sollte Kevin entscheiden, ob der Arbeitsbaum so getaggt werden soll oder ob vorher commit/stash nötig ist. Ich werde keinen `git push` machen.

#### Schritt 2 — Lokale Welt sichern

🖥️ Kevins PC:

```bash
# Beispielpfad prüfen
cd ~/.var/app/org.luanti.luanti/.minetest/worlds
cp -a <WELTNAME> <WELTNAME>.bak-player-migration-$(date +%Y%m%d-%H%M%S)
```

#### Schritt 3 — `avatar.lua` abstrahieren

Datei: `/workspace/chanti-game-mod/avatar.lua`

Ziel:

- Aus dem Modul wird nicht mehr nur "Mob-Avatar", sondern "Chanti-Avatar-Adapter".
- Neue Konfiguration oben:
  - `CHANTI_PLAYER_NAME = "Chanti"`
  - `USE_REAL_PLAYER = true`
  - `ALLOW_ENTITY_FALLBACK = true`
- Neue Funktion `M.get()`:
  1. Erst `minetest.get_player_by_name("Chanti")` prüfen.
  2. Wenn vorhanden: diesen Player-ObjectRef zurückgeben.
  3. Sonst Fallback auf Entity wie bisher.
- Neue Funktion `M.is_real_player()` für Diagnose/State.
- `M.spawn(pos)`:
  - Wenn echter Player online ist: nicht Entity spawnen, sondern optional `player:set_pos(pos)` nur auf expliziten Respawn-Befehl.
  - Wenn nicht online und Fallback aktiv: alte Entity spawnen.
- `M.get_state()` bleibt fast gleich, nutzt aber für Player besser `get_look_horizontal()` wenn verfügbar; sonst `get_yaw()`.
- `M.set_yaw(degrees)` setzt bei Player `set_look_horizontal(math.rad(degrees))`, bei Entity `set_yaw`.
- `M.walk_to(target, callback)`:
  - Für Entity: alte Walk-Logik.
  - Für Player: erstmal keine echte Walk-Physik vortäuschen. Sicherer Minimalansatz: schrittweise `set_pos`/`move_to` über kleine Timer-Intervalle oder kontrolliertes `add_velocity` testen. Ich empfehle für Phase 2 zunächst schrittweises Setzen, klar als "Player-Object gesteuert, nicht Input-Physik" dokumentiert.

Geschätzte Änderung: 80-140 Lua-Zeilen.

#### Schritt 4 — `executor.lua` kompatibel halten

Datei: `/workspace/chanti-game-mod/executor.lua`

Ziel:

- `move_forward` kann weiter `avatar.walk_to()` verwenden.
- Bei Player-Fallback muss `_do_move_forward` keine Details kennen.
- Diagnose-Reason verbessern:
  - "kein Avatar/Player"
  - "Chanti-Player nicht online, Entity-Fallback aktiv"
  - "Player-Bewegung teleportiert/schrittweise"

Geschätzte Änderung: 10-30 Lua-Zeilen.

#### Schritt 5 — `init.lua` Spawn-/Join-Logik ändern

Datei: `/workspace/chanti-game-mod/init.lua`

Ziel:

- Loops nicht bei jedem Join mehrfach starten. Aktuell startet jeder Join `state_loop` und `poll_loop` neu; im Server-Modus mit zwei Playern ist das gefährlich.
- Eine `loops_started`-Variable einführen.
- Beobachter-Privs nur Kevin/alle Nicht-Chanti-Spieler geben; Chanti-Player nicht als Beobachter behandeln.
- Join von `Chanti`:
  - Nametag/Properties falls möglich setzen.
  - Startposition optional in Nähe vom Spawn oder bei letzter Chanti-Position.
- Join von Kevin/anderen Beobachtern:
  - Privs setzen wie bisher.
  - Wenn kein echter Chanti-Player online, Entity-Fallback spawnen.
- Chatcommands anpassen:
  - `/zuchanti` funktioniert mit Player oder Entity.
  - `/chantipos` zeigt zusätzlich `mode=player|entity`.
  - `/respawnchanti` bei echtem Player: nur wenn online, `set_pos`; sonst Entity respawnen.

Geschätzte Änderung: 60-120 Lua-Zeilen.

#### Schritt 6 — `perception.lua` minimal härten

Datei: `/workspace/chanti-game-mod/perception.lua`

Ziel:

- Für Player ggf. `get_look_horizontal()` verwenden, falls vorhanden, weil Player-Yaw-APIs historisch unterschiedlich sind.
- Fallback auf `get_yaw()` für Entity.

Geschätzte Änderung: 5-15 Lua-Zeilen.

#### Schritt 7 — Hostinger-Code nur falls nötig

Dateien:

- `/workspace/chanti/game_tools.py`
- `/workspace/chanti/game_bridge_http.py`
- `/workspace/chanti/game_brain.py`

Voraussichtlich keine Änderung nötig, solange die Aktionsnamen gleich bleiben.

Optional könnte der State später `mode = "player" | "entity"` enthalten. Das wäre nützlich fürs Debugging, aber nicht zwingend.

#### Schritt 8 — Test-Doku in Phase 3

MIGRATION_TEST.md beschreibt dann:

- `chanti-pull` auf Kevins PC.
- Welt-Backup.
- Server starten.
- Als `Chanti` verbinden oder nicht, je nach Variante.
- Als Kevin beobachten.
- Chatcommands prüfen.
- Logs prüfen.

## Risiken und Stolpersteine

### Größtes Risiko: Erwartung "synthetischer Player"

Die API kann keinen synthetischen Player erzeugen. Wenn das harte Ziel "Chanti ist echter Player ohne zweiten Client" ist, brauchen wir Plan B außerhalb von Lua:

- Externer Bot-Client,
- Engine-Patch,
- oder Akzeptanz eines Player-like Entity-Avatars.

### Player-Bewegung

- Server-Lua kann Player-Input lesen, aber nicht setzen.
- `set_velocity` geht nicht für Player; `add_velocity` nur Impulse.
- `move_to` ist bei Playern kein Smooth-Move, sondern wie `set_pos`.
- Teleportartige Bewegung kann Anti-Cheat-/Moved-too-fast-Warnungen auslösen, je nach Server-Einstellungen.

### Mehrere Join-Events

Aktueller Code startet Loops in `register_on_joinplayer`; bei Server-Modus und mehreren Spielern drohen doppelte State-/Poll-Loops. Das muss in Phase 2 gefixt werden, unabhängig von Player/Mob.

### Bestehende Welt

- Falscher Flatpak-Datenpfad kann dazu führen, dass Kevin scheinbar eine neue Welt startet.
- Mod-Konfigurationsfehler kann Weltstart verhindern.
- Alte persistente Chanti-Entities (`static_save = true`) können als Geister in gespeicherten Mapblocks existieren. Der aktuelle Spawn-Code räumt nur in Radius 100 um Spawnposition auf. Migration sollte einen expliziten Cleanup/Respawn-Befehl behalten.

### Performance

- Ein echter zweiter Client kostet mehr als eine Entity: Netzwerk, Player-Objekt, Media/State, Rendering lokal.
- Die Mod selbst pollt weiter alle 1.5s und sendet State jede Sekunde. Bei zwei Playern ist das okay, wenn Loops nur einmal laufen.
- Externe Bot-Clients könnten später deutlich mehr Komplexität/Fehlerquellen bringen.

## Rollback-Strategie

### Git-Rollback

☁️ Hostinger / Repo:

Vor Phase 2:

```bash
cd /workspace/chanti
git status --short
git tag pre-player-migration-20260429
```

Rollback auf Tag:

```bash
cd /workspace/chanti
git checkout pre-player-migration-20260429 -- /workspace/chanti-game-mod/init.lua /workspace/chanti-game-mod/avatar.lua /workspace/chanti-game-mod/executor.lua /workspace/chanti-game-mod/perception.lua
```

Falls Hostinger-Python-Dateien geändert wurden:

```bash
git checkout pre-player-migration-20260429 -- server.py game_bridge_http.py game_brain.py game_tools.py
sudo systemctl restart chanti
sudo journalctl -u chanti -n 30 --no-pager
```

Hinweis: Repo-Toplevel ist `/workspace/chanti`; `/workspace/chanti-game-mod` liegt außerhalb dieses Git-Repos, falls dort kein eigenes Git-Repo existiert. Deshalb zusätzlich `.bak`-Dateien in Phase 2 zwingend.

### Datei-Backups

Phase 2-Regel:

- Vor jeder Änderung: `datei.lua.bak` anlegen.
- Rollback:

```bash
cp /workspace/chanti-game-mod/avatar.lua.bak /workspace/chanti-game-mod/avatar.lua
cp /workspace/chanti-game-mod/executor.lua.bak /workspace/chanti-game-mod/executor.lua
cp /workspace/chanti-game-mod/init.lua.bak /workspace/chanti-game-mod/init.lua
cp /workspace/chanti-game-mod/perception.lua.bak /workspace/chanti-game-mod/perception.lua
```

### Lokale Welt zurücksetzen

🖥️ Kevins PC:

```bash
# Server/Luanti vorher schließen
cd ~/.var/app/org.luanti.luanti/.minetest/worlds
mv <WELTNAME> <WELTNAME>.failed-player-migration-$(date +%Y%m%d-%H%M%S)
mv <WELTNAME>.bak-player-migration-<TIMESTAMP> <WELTNAME>
```

### Mod-Sync zurückrollen

🖥️ Kevins PC:

- Nach Hostinger-Rollback `chanti-pull` ausführen, damit die lokale Mod-Kopie wieder den alten Stand bekommt.
- Welt starten und `/respawnchanti` testen.

## Geschätzter Aufwand

Für Variante C (Hybrid Player/Entity-Fallback):

- Lua-Code: ca. 160-300 geänderte/neu strukturierte Zeilen.
- Python-Code Hostinger: 0-20 Zeilen optional für Debug-Mode im State.
- Iterationen:
  1. Compile-/Syntax-Fix der Lua-Dateien.
  2. Single-observer Test ohne Chanti-Player: Entity-Fallback muss weiter laufen.
  3. Server-Test mit Chanti-Player online: Player-Referenz, Position, Perception, Chatcommands.
  4. Bewegungs-Tuning: wahrscheinlich 1-2 Zusatziterationen, weil Player-Bewegung per Server-Lua eingeschränkt ist.

Für echten Bot-Client später:

- Unklar, deutlich größer: externe Client-Steuerung, Auth, Netzwerk, Prozessmanagement, Protokoll/Modchannel oder Headless-Automation.
- Nicht sinnvoll in dieser 4-Phasen-Migration ohne separaten Plan.

## Offene Fragen an Kevin

1. Ist "echter Player" eine harte Anforderung, auch wenn dafür ein echter zweiter Client/Bot-Client nötig ist?
2. Oder reicht als nächster Schritt eine hybride Lösung: echter `Chanti`-Player wenn online, sonst Entity-Fallback?
3. Soll Chanti später wirklich natürliche WASD-Player-Physik nutzen, oder ist schrittweise serverseitige Bewegung/Teleport für Phase 2 akzeptabel?
4. Wie heißt deine bestehende Welt genau im Luanti/Flatpak-Weltordner?
5. Wie soll der Beobachter-Account heißen? Soll nur dieser Account `fly/fast/noclip` bekommen oder weiterhin jeder Nicht-Chanti-Spieler?
6. Nutzt deine Welt Minetest Game mit `player_api`? Falls ja, können wir Chanti optisch sauberer als Player-Mesh darstellen; falls nein, müssen wir beim simplen Visual bleiben oder Abhängigkeit hinzufügen.
7. Darf Phase 2 eine Hybrid-Lösung implementieren, oder soll ich stoppen und erst einen separaten Plan für externen Bot-Client machen?
