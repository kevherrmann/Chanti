#!/bin/bash

echo "🟣 Starte Chanti..."

# Home Assistant starten
echo "🏠 Starte Home Assistant..."
docker start homeassistant 2>/dev/null || echo "Home Assistant läuft bereits"

# ngrok starten
echo "🌐 Starte ngrok..."
nohup ngrok http 5678 --log=stdout > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!

# Warten bis ngrok bereit ist
echo "⏳ Warte auf ngrok..."
for i in {1..10}; do
    NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" 2>/dev/null)
    if [ -n "$NGROK_URL" ]; then
        echo "✅ ngrok URL: $NGROK_URL"
        break
    fi
    sleep 2
done

# n8n starten
echo "⚙️  Starte n8n..."
NODE_FUNCTION_ALLOW_EXTERNAL=axios,form-data WEBHOOK_URL=$NGROK_URL n8n start &
N8N_PID=$!

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

# Chanti Web-Server starten
echo "🌐 Starte Chanti Web-UI auf http://localhost:8000"
cd ~/chanti
conda run -p /run/media/z0mb1/58BCF437BCF4116C/chanti-env \
    uvicorn server:app --host 0.0.0.0 --port 8000

# Cleanup beim Beenden
trap "kill $XTTS_PID $N8N_PID $NGROK_PID 2>/dev/null" EXIT
