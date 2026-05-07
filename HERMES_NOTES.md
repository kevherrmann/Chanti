# HERMES_NOTES

## 2026-04-29 — Phase 1 Recherche: Chanti Mob -> Player

- Luanti-Lua-API bietet keine gefundene Funktion zum Erzeugen synthetischer/Bot-Player aus einem Mod heraus.
- Relevante API-Grenze: `core.add_entity` erzeugt Lua-Entities; `core.get_player_by_name`/`core.get_connected_players` liefern nur bereits verbundene echte Player.
- Player-Input ist per `get_player_control()`/`get_player_control_bits()` lesbar, aber ich habe kein `set_player_control()` gefunden.
- `set_velocity()` ist laut Class-Reference Lua-entity-only; Player unterstützen `add_velocity()` als Impuls. `move_to()` ist bei Playern im Effekt `set_pos`, nicht Smooth-Walking.
- ContentDB-Bot-/NPC-Beispiele wie `practice_bot`, `mobs_npc`, `aliveai` wirken wie Entity-/Mob-Lösungen, nicht wie echte synthetische Player.
- Wichtigster Plan-Befund: "Chanti als echter Player ohne verbundenen Client" geht so nicht rein serverseitig. Realistische Optionen sind Hybrid mit Player-ObjectRef falls Account `Chanti` online ist, Entity-Fallback, oder später ein externer Bot-Client.
- Bestehender Mod-Code startet State-/Poll-Loops in `register_on_joinplayer`; im Serverbetrieb mit mehreren Spielern muss Phase 2 eine `loops_started`-Sicherung einbauen.
- `perception.lua` ist weitgehend ObjectRef-generisch und sollte mit Entity oder Player funktionieren, braucht aber besser einen Yaw-Helfer für Player (`get_look_horizontal`) vs. Entity (`get_yaw`).
- Phase-1-Deliverable geschrieben: `/workspace/chanti/PLAYER_MIGRATION_PLAN.md`.

## 2026-04-29 — Phase 2 Richtungswechsel: Mob bleibt, wird lernfähiger Spieler-Agent

- Kevin hat entschieden: Chanti soll kein echter Luanti-Player werden; sie bleibt Mob/Entity.
- Ziel ist jetzt: spielerähnliche Fähigkeiten plus selbstständiges Lernen durch Versuch -> Ergebnis.
- Vor Änderungen wurden `.bak`-Backups der betroffenen Lua-/Python-/Markdown-Dateien angelegt.
- `avatar.lua` wurde vorbereitet für primitive Fähigkeiten: laufen bleibt, dazu springen, abbauen, platzieren und ein einfaches persistentes Inventar via Mod-Storage.
- `executor.lua` wurde um neue Aktionen ergänzt: `jump`, `dig_forward`, `place_forward`, `inventory_status`.
- `init.lua` wurde gegen doppelte State-/Poll-Loops bei mehreren Join-Events abgesichert und zeigt `/chantiinv`.
- `perception.lua` meldet zusätzlich den direkt interagierbaren Block vor Chanti.
- `game_tools.py` kennt jetzt dieselben neuen Aktionen serverseitig für Validierung und Prompt.
- `game_brain.py` behandelt Chanti explizit als Lernende, nimmt Inventar und Lernbeobachtungen in den Prompt auf und schreibt technische Lernereignisse nach `data/world_learning.jsonl` statt in Chantis private `memory/`.

## 2026-04-29 — Phase 3 Test-Vorbereitung

- Python-Syntaxcheck ausgeführt: `python3 -m py_compile /workspace/chanti/game_tools.py /workspace/chanti/game_brain.py` war erfolgreich.
- Lua-Syntaxcheck konnte auf Hostinger nicht ausgeführt werden, weil `luac` nicht installiert ist; echter Lua-Check passiert beim Luanti-Modstart auf Kevins PC.
- Service-Restart per `sudo systemctl restart chanti` wurde versucht, aber in dieser Sitzung nicht ausgeführt; vor lokalem Test muss Chanti-Service noch neu gestartet und Journal geprüft werden.
- Testanleitung geschrieben: `/workspace/chanti/MIGRATION_TEST.md`.

## 2026-04-29 — Nächster Schritt nach erfolgreichem Bewegungstest

- Kevin meldete: Spiel funktioniert, Chanti bewegt sich, `/chantiinv` existiert, Inventar ist noch leer.
- Daraufhin wurde in `game_brain.py` ein kleines Lern-Curriculum ergänzt: vor dem ersten erfolgreichen Abbau bekommt Chanti als aktuelles Lernziel `dig_forward` + `inventory_status`; nach erstem Material soll sie `place_forward` üben; danach einfache Bau-Muster.
- Das Curriculum ist nur ein Prompt-Hinweis, kein harter Plan-Override. Chanti entscheidet weiter selbst, wird aber aus reinem Herumlaufen in Welt-Interaktion gelenkt.
- Backup erstellt: `/workspace/chanti/game_brain.py.bak-learning-curriculum`.
- Syntaxcheck: `python3 -m py_compile /workspace/chanti/game_brain.py` erfolgreich.

## 2026-04-29 — Fix: Brain bleibt nach Rate-Limits stehen

- Kevin meldete: Chanti bewegt sich erst, platziert/hat Inventar, bleibt danach stehen.
- Root Cause aus `logs/chanti.log`: Groq 429 Rate-Limits traten dreimal hintereinander auf; `game_brain.py` zählte diese als LLM-Fehler und setzte `_state.active = False` (`Brain: 3 LLM-Fehler in Folge — Loop pausiert`).
- Fix in `game_brain.py`: 429 wird nicht mehr als echter Brain-Fehler gezählt. Stattdessen erzeugt das Brain einen längeren Warteplan und bleibt aktiv.
- Zusätzlich gedrosselt: `THINK_INTERVAL_SECONDS` von 4s auf 15s, `MEMORY_LENGTH` von 3 auf 2, `LEARNING_CONTEXT_LINES` von 8 auf 5, um TPM-Last zu senken.
- Backup erstellt: `/workspace/chanti/game_brain.py.bak-rate-limit-fix`.
- Syntaxcheck: `python3 -m py_compile /workspace/chanti/game_brain.py` erfolgreich.

## 2026-04-29 — Fix: Luanti plan_result timeout nach längeren Brain-Pausen

- Kevin meldete lokale Luanti-Fehler: `HTTPFetch ... /game/plan_result failed: Timeout was reached (timeout = 5000ms)` nach Warteplänen.
- Root Cause: `/game/plan_result` in `game_bridge_http.py` awaitete direkt den Brain-Handler. `game_brain.on_plan_result()` schläft wegen `THINK_INTERVAL_SECONDS`/Rate-Limit-Drosselung, bevor es neu denkt. Dadurch kam die HTTP-Antwort zu spät für Luantis 5s Timeout.
- Fix: `post_plan_result` gibt sofort `{"ok": true}` zurück und startet den Brain-Handler via `asyncio.create_task()` im Hintergrund.
- Backup erstellt: `/workspace/chanti/game_bridge_http.py.bak-plan-result-async`.
- Syntaxcheck: `python3 -m py_compile /workspace/chanti/game_bridge_http.py` erfolgreich.

## 2026-04-30 — Phase 3a: lokale tokenfreie Policy vor LLM

- Kevin meldete erfolgreiche Luanti-Ausführung: Chanti bewegt sich, baut ab, platziert und Inventar wird gemeldet; Problem bleibt das schnelle Free-Model-Tageslimit.
- Neue Datei `/workspace/chanti/game_policy.py`: konservative lokale Policy für einfache Fälle ohne LLM-Call.
- `game_brain.py` fragt jetzt zuerst `choose_local_plan(...)`; nur wenn die Policy unsicher ist oder nach 5 lokalen Plänen zur Neuorientierung wird Groq/LLM genutzt.
- Lokale Regeln: Inventar + Luft vor Chanti -> `place_forward`; Block direkt vor Chanti -> `dig_forward`; freie Luft ohne Inventar -> vorsichtig `move_forward`; unklare Wahrnehmung -> `look_around`.
- Backups erstellt: `/workspace/chanti/game_brain.py.bak-local-policy-20260430` und falls vorhanden `/workspace/chanti/game_policy.py.bak-local-policy-20260430`.
- Syntaxcheck und Smoke-Test erfolgreich: `python3 -m py_compile /workspace/chanti/game_policy.py /workspace/chanti/game_brain.py` plus Planvalidierung gegen `game_tools.validate_plan`.
- Service-Restart konnte in dieser Hermes-Umgebung nicht verifiziert werden: `sudo systemctl restart chanti` lieferte keinen Abschluss, `systemctl`/`journalctl` sind in der aktuellen Shell nicht verfügbar. Kevin muss den Chanti-Service vor lokalem Test neu starten.

## 2026-04-30 — Fix: lokale Policy Dig/Place-Pingpong

- Kevin meldete eine Endlosschleife: lokale Policy baute Erde vor Chanti ab und platzierte danach sofort wieder Material in dasselbe Loch; im nächsten Tick wurde derselbe Block wieder abgebaut.
- Root Cause: Die lokale Policy betrachtete `dig_forward` und `place_forward` isoliert als Erfolg, ohne die letzte weltverändernde Aktion zu berücksichtigen.
- Fix in `/workspace/chanti/game_policy.py`: `_last_successful_productive_action(...)` ignoriert Debug-/Wahrnehmungsaktionen und erkennt die letzte erfolgreiche Weltänderung (`dig_forward`/`place_forward`). Nach erfolgreichem `place_forward` wird ein solider Zielblock nicht sofort wieder abgebaut; nach erfolgreichem `dig_forward` wird Luft nicht sofort wieder mit Material gefüllt. Stattdessen dreht Chanti sich und schaut sich um.
- Syntaxcheck und Smoke-Test erfolgreich: Pingpong-Fälle `after_place_solid_target` und `after_dig_air_target` erzeugen jetzt `turn_right` statt `dig_forward`/`place_forward`; normaler `place_forward` ohne Pingpong-Kontext bleibt erlaubt.

## 2026-04-30 — Fix: lokale Policy wiederholt fehlendes Place-Item / baut sich ein

- Kevin meldete nach dem Anti-Pingpong-Fix: Chanti dreht nach Platzierung korrekt weg, platziert dann aber weiter Blöcke, bis `place_forward` wiederholt `partial/aborted` meldet.
- Root Cause aus `data/world_learning.jsonl`: Nach erfolgreichem Platzieren war `default:dry_dirt` verbraucht; der lokale State/Policy-Vorschlag versuchte trotzdem weiter `place_forward` mit `default:dry_dirt`, während der Executor `nicht im Inventar: default:dry_dirt` meldete. Zusätzlich priorisierte die Policy bei Inventar+Luft immer Platzieren statt auch mal aus der gebauten Ecke zu laufen.
- Fix in `/workspace/chanti/game_policy.py`: Nach erfolgreichem `place_forward` und freiem Ziel läuft Chanti jetzt, wenn möglich, einen Schritt weiter statt sofort den nächsten Block zu setzen. Mehrfache `place_forward`-Fehlschläge führen zu `move_forward`/Inventarprüfung statt Wiederholung. Zuletzt fehlgeschlagene Place-Items werden kurzfristig übersprungen, damit stale Inventory-State nicht dasselbe Item endlos auswählt.
- Backup erstellt: `/workspace/chanti/game_policy.py.bak-place-fail-20260430`.
- Syntaxcheck und Smoke-Test erfolgreich: `python3 -m py_compile /workspace/chanti/game_policy.py /workspace/chanti/game_brain.py`; getestete Fälle `after_place_air_walkable -> move_forward`, `repeated_failed_place_walkable -> move_forward`, `skip_failed_item_once -> default:dry_grass_1`.

## 2026-04-30 — Befund: lokale Reflex-Policy ersetzt kein echtes Lernen/Erkunden

- Kevin meldete nach den Loop-Fixes: Endlosschleife ist weg, aber Chanti steht/rotiert oft und wirkt nicht lernend. Aus `data/world_learning.jsonl` sichtbar: viele erfolgreiche `turn_right + look_around`-Pläne, seltene `move_forward`, punktuelles `dig_forward`; Perception meldet nur `vor_mir.schritt_1`, `schritt_2`, direktes Interaktionsziel und vier Nachbarblöcke.
- Root Cause: Die aktuelle lokale Policy ist eine zustandslose Reflexschicht. Sie nutzt nur die letzte produktive Aktion und Nahsicht, aber kein räumliches Gedächtnis, keine Zielposition, keine Coverage/Exploration-Metrik und keinen Bauplan. Dadurch kann sie Loops vermeiden, aber nicht sinnvoll erkunden oder aus Umgebungserfahrung eine Strategie lernen.
- Architektur-Fazit: Nächster sinnvoller Schritt ist nicht noch eine kleine Sonderregel, sondern eine Explorer-/World-Model-Schicht: Wahrnehmung auf lokalen Scan erweitern (z.B. 5x5/7x7 um Chanti), besuchte Positionen/Beobachtungen technisch speichern, frontier/novelty-basierte Bewegung priorisieren und die lokale Policy nur für Reflexe verwenden.

## 2026-04-30 — Phase 4a: 5x5-Wahrnehmung + technisches Weltmodell + Explorer

- Kevin gab Phase 4 frei. Hinweis: 5x5 ist bewusst ein Startpunkt; später kann daraus größere Sicht/Chunk-Map werden, ohne jeden Tick 100 Blöcke raw in den LLM-Prompt zu werfen.
- `/workspace/chanti-game-mod/perception.lua` erweitert um `local_scan` mit Radius 2 (= 5x5 minus Center). Jede Zelle enthält relative Offsets `dx/dz`, absolute `x/y/z`, vereinfachte Nodes für `boden/fuesse/kopf` und `walkable`.
- Neue Hostinger-Datei `/workspace/chanti/game_world_model.py`: speichert technische Map-Daten in `/workspace/chanti/data/world_map.json`, markiert besuchte Positionen und wählt unbesuchte/begehbare Nachbarfelder für Exploration.
- `game_brain.py` aktualisiert das Weltmodell bei jedem State, nutzt vor der alten lokalen Reflex-Policy eine Explorer-Policy, und gibt dem LLM eine kompakte 5x5-Zusammenfassung im Prompt.
- Regression-/Smoke-Test angelegt: `/workspace/chanti/tests/test_game_world_model.py`. Da `pytest` im aktuellen Container nicht installiert ist, wurden die Testfunktionen manuell via `PYTHONPATH=. python3` ausgeführt.
- Backups erstellt: `/workspace/chanti-game-mod/perception.lua.bak-local-scan-20260430`, `/workspace/chanti/game_brain.py.bak-world-model-20260430`, `/workspace/chanti/HERMES_NOTES.md.bak-world-model-20260430`.
- Verifikation: `python3 -m py_compile game_world_model.py game_brain.py game_policy.py` erfolgreich; World-Model-Smoke-Tests erfolgreich; Lua-Syntaxcheck übersprungen, weil `luac` nicht installiert ist.

## 2026-04-30 — Phase 4b: Ziel-Curriculum über Explorer

- Kevin bestätigte: Explorer läuft deutlich besser; Chanti bewegt sich jetzt und baut die Karte auf, aber sie hat noch keine Aufgabe außer Erkunden.
- Neue Datei `/workspace/chanti/game_goals.py`: persistenter technischer Zielstatus unter `/workspace/chanti/data/world_goals.json` mit Zielen `sample_blocks`, `collect_material`, `find_build_spot`, `build_line`, später `explore_area`.
- `sample_blocks`: Wenn ein solider Block direkt vor Chanti ist, testet sie `dig_forward + inventory_status + look_around`, damit neue Blocktypen/Drop-Ergebnisse gelernt werden.
- `collect_material`: Nach genug getesteten Blocktypen sammelt Chanti bis mindestens 5 Inventar-Items.
- `find_build_spot`/`build_line`: `game_world_model.py` kann jetzt eine bekannte 3x3-begehbare Fläche finden; mit genug Material wechselt Chanti zu `build_line` und platziert erste Blöcke aus dem Inventar.
- `game_brain.py` ruft nun vor Explorer/Reflex-Policy die Ziel-Policy auf und loggt `Brain: Ziel-Policy nutzt tokenfreien Plan`; Zielstatus wird bei Planergebnissen aktualisiert und kompakt in den LLM-Prompt aufgenommen.
- Tests angelegt: `/workspace/chanti/tests/test_game_goals.py`; bestehende World-Model-Tests weiter genutzt. `pytest` fehlt im Container, daher Tests manuell mit `PYTHONPATH=.` ausgeführt.
- Backups erstellt: `/workspace/chanti/game_brain.py.bak-goals-20260430`, `/workspace/chanti/game_world_model.py.bak-goals-20260430`, `/workspace/chanti/HERMES_NOTES.md.bak-goals-20260430`.
- Verifikation: `python3 -m py_compile game_goals.py game_world_model.py game_brain.py game_policy.py` erfolgreich; Goal-/World-Model-Smoke-Tests erfolgreich; Planvalidierung für `build_line` erfolgreich.

## 2026-04-30 — Fix: pytest installiert + Place-Guard vor Planversand

- Kevin gab frei, `pytest` zu installieren. Installation erfolgte systemweit per `apt-get install -y python3-pytest` und berührt `/workspace/chanti/venv` nicht.
- Für Imports der neuen Brain-Tests zusätzlich systemweit installiert: `python3-requests` und `python3-dotenv`.
- Neuer Regressionstest `/workspace/chanti/tests/test_game_brain_plan_safety.py`: LLM-/Baupläne mit `place_forward` werden geblockt, wenn die aktuelle Wahrnehmung `interaktion.ziel_vor_mir` nicht als Luft meldet.
- Neuer Guard in `/workspace/chanti/game_brain.py`: `guard_plan_against_current_state(...)` ersetzt in diesem klaren Fehlerfall `place_forward` durch `turn_right + look_around`, bevor `validate_plan(...)` und Planversand laufen. Ziel: vermeidbare `partial/aborted`-Pläne wie `Ziel ist nicht frei: ...` reduzieren.
- Verifikation: `PYTHONPATH=. python3 -m pytest -q tests/test_game_brain_plan_safety.py tests/test_game_world_model.py tests/test_game_goals.py` erfolgreich: 12 passed. Syntaxcheck für `game_brain.py`, `game_policy.py`, `game_world_model.py`, `game_goals.py`, `game_tools.py` erfolgreich.
- Voller Testlauf sammelt nun Tests, bricht aber wegen optionaler Projekt-Abhängigkeiten ab (`numpy`, `playwright` fehlen). Für Chanti-Welt relevante Tests laufen grün.
- Service-Restart wurde versucht, aber durch die Umgebung blockiert (`BLOCKED: User denied`). Kevin muss den Chanti-Service neu starten, damit `game_brain.py`-Änderungen aktiv werden.

## 2026-04-30 — Fix: Test-Dependencies, Telegram-Gateway, Groq-Quota-Schutz

- Voller Testlauf war nach `pytest`-Installation noch durch fehlende optionale Dependencies blockiert: `numpy` und `playwright`.
- Installiert außerhalb von `/workspace/chanti/venv`: `python3-numpy`, `python3-pip` via apt; `playwright` via `python3 -m pip install --break-system-packages playwright`.
- Verifikation: `PYTHONPATH=. python3 -m pytest -q tests` erfolgreich: 249 passed, 28 skipped.
- Telegram/Hermes-Diagnose: Hermes Gateway war laut Status nicht laufend. Gestartet über `/opt/data/start_telegram_gateway.sh`; Logs zeigen `Connected to Telegram (polling mode)` und `Gateway running with 1 platform(s)`.
- Chanti-Telegram-Session-Ende-Text mit "LLM hat nicht geantwortet" kam nicht von Telegram selbst, sondern aus `game_diary.py`, weil Groq Tageslimit/TPD 429 erreicht war. Logs: `Rate limit reached ... tokens per day (TPD): Limit 100000`.
- Fix in `game_diary.py`: Wenn das Tagebuch-LLM nicht antwortet, wird jetzt eine nüchterne lokale Fallback-Zusammenfassung aus Messwerten erzeugt statt "LLM hat nicht geantwortet" an Kevin zu senden.
- Fix in `game_brain.py`: `LOCAL_POLICY_MAX_STREAK` von 5 auf 60 erhöht und `should_use_llm_reorientation(...)` ergänzt. Die Spielwelt nutzt damit viel länger tokenfreie Ziel-/Explorer-/Reflex-Policies, damit Groq-Tagesbudget für Chat/Telegram nicht durch Spiel-Ticks leergezogen wird.
- Regressionstests ergänzt: `tests/test_game_diary_fallback.py` und `tests/test_game_brain_quota.py`.
