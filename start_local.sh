#!/bin/bash

echo "🟣 Starte lokale Chanti-Dienste..."

# Cleanup-Handler
cleanup() {
    echo "🛑 Beende alle Dienste..."
    [ -n "$XTTS_PID" ]     && kill $XTTS_PID 2>/dev/null
    [ -n "$WAKEWORD_PID" ] && kill $WAKEWORD_PID 2>/dev/null
    echo "✅ Cleanup abgeschlossen"
}
trap cleanup EXIT INT TERM

# Home Assistant starten
echo "🏠 Starte Home Assistant..."
docker start homeassistant 2>/dev/null || echo "Home Assistant läuft bereits"

# XTTS Server auf GPU starten
echo "🔊 Starte XTTS Server (GPU)..."
/run/media/z0mb1/58BCF437BCF4116C/xtts-env/bin/python \
    /run/media/z0mb1/58BCF437BCF4116C/tts_server.py &
XTTS_PID=$!

# Warten bis XTTS bereit ist
echo "⏳ Warte auf XTTS..."
until curl -s -X POST http://127.0.0.1:5500 -d "test" --output /dev/null 2>/dev/null; do
    sleep 2
done
echo "✅ XTTS bereit"

# Wake Word Listener starten (verbindet sich gegen Hostinger)
echo "🎤 Starte Wake Word Listener..."
cd ~/chanti
PYTHONUNBUFFERED=1 /run/media/z0mb1/58BCF437BCF4116C/chanti-env/bin/python \
    wakeword.py &
WAKEWORD_PID=$!
echo "✅ Wake Word bereit"

echo ""
echo "✅ Alle lokalen Dienste gestartet."
echo "   Chanti läuft auf: http://76.13.140.227:8000"
echo "   n8n läuft auf:    https://reentry-sixtyfold-strep.ngrok-free.dev"
echo ""

# Blockieren bis Ctrl+C
wait $WAKEWORD_PID
