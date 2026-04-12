from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from llm import chat as llm_chat
from tts import speak
from memory import load
from memory_extractor import extract_and_save
from text_utils import clean_for_tts
from actions import detect_and_execute
from pathlib import Path
from config import SOUL_FILE
import asyncio
import base64
import numpy as np
import resampy
import soundfile as sf
import io

from faster_whisper import WhisperModel as _WhisperModel
_whisper = _WhisperModel("base", device="cpu", compute_type="int8")

app = FastAPI()
active_connections = []

async def broadcast_notify(message: str):
    for ws in active_connections:
        try:
            await ws.send_json({"type": "message", "text": message})
        except:
            pass
    import asyncio
    await asyncio.get_event_loop().run_in_executor(None, lambda: speak(clean_for_tts(message)))

soul = Path(SOUL_FILE).read_text(encoding="utf-8")
soul = soul.replace("{memory}", load())

HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chanti</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0f0f1a;
            color: #e0e0ff;
            font-family: 'Segoe UI', sans-serif;
            height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        header {
            width: 100%;
            padding: 20px;
            text-align: center;
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            border-bottom: 1px solid #2a2a4a;
        }
        header h1 { font-size: 1.8rem; color: #a78bfa; letter-spacing: 3px; }
        header p { font-size: 0.8rem; color: #6b7280; margin-top: 4px; }
        #chat {
            flex: 1;
            width: 100%;
            max-width: 800px;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .msg {
            max-width: 75%;
            padding: 12px 16px;
            border-radius: 18px;
            line-height: 1.5;
            font-size: 0.95rem;
        }
        .user {
            align-self: flex-end;
            background: #4c1d95;
            color: #ede9fe;
            border-bottom-right-radius: 4px;
        }
        .chanti {
            align-self: flex-start;
            background: #1e1b4b;
            color: #c4b5fd;
            border-bottom-left-radius: 4px;
            border: 1px solid #2e2b6b;
        }
        .chanti .name {
            font-size: 0.7rem;
            color: #7c3aed;
            margin-bottom: 4px;
            font-weight: bold;
            letter-spacing: 1px;
        }
        .thinking {
            align-self: flex-start;
            color: #6b7280;
            font-style: italic;
            font-size: 0.85rem;
            padding: 8px 16px;
        }
        #inputarea {
            width: 100%;
            max-width: 800px;
            padding: 16px;
            display: flex;
            gap: 10px;
            align-items: center;
        }
        #input {
            flex: 1;
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            color: #e0e0ff;
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 0.95rem;
            outline: none;
            transition: border 0.2s;
        }
        #input:focus { border-color: #7c3aed; }
        #send {
            background: #7c3aed;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 12px;
            cursor: pointer;
            font-size: 0.95rem;
            transition: background 0.2s;
        }
        #send:hover { background: #6d28d9; }
        #send:disabled { background: #374151; cursor: not-allowed; }
        #micbtn {
            background: #1e1b4b;
            border: 2px solid #7c3aed;
            color: #a78bfa;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 1.2rem;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #micbtn.recording {
            background: #7c3aed;
            color: white;
            animation: pulse 1s infinite;
        }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(124,58,237,0.7); }
            70% { box-shadow: 0 0 0 10px rgba(124,58,237,0); }
            100% { box-shadow: 0 0 0 0 rgba(124,58,237,0); }
        }
        #status {
            font-size: 0.75rem;
            color: #6b7280;
            padding: 4px 16px 8px;
        }
    </style>
</head>
<body>
    <header>
        <h1>✦ CHANTI ✦</h1>
        <p>Deine persönliche KI-Assistentin</p>
    </header>
    <div id="chat"></div>
    <div id="status">Verbinde...</div>
    <div id="inputarea">
        <input id="input" type="text" placeholder="Schreib etwas..." autocomplete="off"/>
        <button id="micbtn" title="Halten zum Sprechen">🎤</button>
        <button id="send" disabled>Senden</button>
    </div>

    <script>
        const chat = document.getElementById('chat');
        const input = document.getElementById('input');
        const send = document.getElementById('send');
        const status = document.getElementById('status');
        const micbtn = document.getElementById('micbtn');

        const ws = new WebSocket(`ws://${location.host}/ws`);
        let mediaRecorder = null;
        let audioChunks = [];
        let isRecording = false;

        ws.onopen = () => {
            status.textContent = 'Verbunden ✓';
            send.disabled = false;
        };
        ws.onclose = () => {
            status.textContent = 'Verbindung getrennt';
            send.disabled = true;
        };

        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            const thinking = document.getElementById('thinking');
            if (thinking) thinking.remove();
            if (data.type === 'message') {
                addMessage('chanti', data.text);
                send.disabled = false;
                input.focus();
            } else if (data.type === 'transcript') {
                addMessage('user', data.text);
                addThinking();
            }
        };

        function addMessage(role, text) {
            const div = document.createElement('div');
            div.className = `msg ${role}`;
            if (role === 'chanti') {
                div.innerHTML = `<div class="name">CHANTI</div>${text}`;
            } else {
                div.textContent = text;
            }
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        function addThinking() {
            const div = document.createElement('div');
            div.className = 'thinking';
            div.id = 'thinking';
            div.textContent = 'Chanti denkt nach...';
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        function sendMessage() {
            const text = input.value.trim();
            if (!text) return;
            addMessage('user', text);
            addThinking();
            ws.send(JSON.stringify({type: 'text', text}));
            input.value = '';
            send.disabled = true;
        }

        send.onclick = sendMessage;
        input.onkeydown = (e) => { if (e.key === 'Enter') sendMessage(); };

        // Push-to-Talk
        async function startRecording() {
            const stream = await navigator.mediaDevices.getUserMedia({audio: true});
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
            mediaRecorder.onstop = async () => {
                const blob = new Blob(audioChunks, {type: 'audio/webm'});
                const reader = new FileReader();
                reader.onloadend = () => {
                    const b64 = reader.result.split(',')[1];
                    ws.send(JSON.stringify({type: 'audio', data: b64}));
                };
                reader.readAsDataURL(blob);
                stream.getTracks().forEach(t => t.stop());
            };
            mediaRecorder.start();
            isRecording = true;
            micbtn.classList.add('recording');
            status.textContent = '🔴 Aufnahme läuft...';
        }

        function stopRecording() {
            if (mediaRecorder && isRecording) {
                mediaRecorder.stop();
                isRecording = false;
                micbtn.classList.remove('recording');
                status.textContent = 'Verbunden ✓';
            }
        }

        micbtn.addEventListener('mousedown', startRecording);
        micbtn.addEventListener('mouseup', stopRecording);
        micbtn.addEventListener('touchstart', e => { e.preventDefault(); startRecording(); });
        micbtn.addEventListener('touchend', e => { e.preventDefault(); stopRecording(); });
    </script>
</body>
</html>
"""

@app.get("/")
async def index():
    return HTMLResponse(HTML)


@app.post("/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    text = data.get("message", "")
    if not text:
        return {"response": "Keine Nachricht erhalten."}
    
    history = [{"role": "system", "content": soul}]
    history.append({"role": "user", "content": text})
    
    response = await asyncio.get_event_loop().run_in_executor(
        None, lambda: llm_chat(history)
    )
    extract_and_save(text, response)
    return {"response": response}

@app.post("/notify")
async def notify(request: Request):
    data = await request.json()
    message = data.get("message", "")
    if message:
        asyncio.create_task(broadcast_notify(message))
    return {"ok": True}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    history = [{"role": "system", "content": soul}]

    async def process_text(text: str, use_tts: bool = False):
        history.append({"role": "user", "content": text})
        action_result = detect_and_execute(text)
        if action_result:
            response = action_result
        else:
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm_chat(history)
            )
        history.append({"role": "assistant", "content": response})
        await websocket.send_json({"type": "message", "text": response})
        if use_tts:
            await asyncio.get_event_loop().run_in_executor(None, lambda: speak(clean_for_tts(response)))
        extract_and_save(text, response)

    try:
        while True:
            raw = await websocket.receive_text()
            data = __import__('json').loads(raw)

            if data['type'] == 'text':
                await process_text(data['text'], use_tts=False)

            elif data['type'] == 'audio':
                import tempfile, os
                audio_bytes = base64.b64decode(data['data'])
                with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
                    f.write(audio_bytes)
                    tmp_path = f.name
                wav_path = tmp_path.replace('.webm', '.wav')
                try:
                    os.system(f'ffmpeg -i {tmp_path} -ar 16000 -ac 1 {wav_path} -y -loglevel quiet')
                    segments, _ = _whisper.transcribe(wav_path, language="de", beam_size=1)
                    text = " ".join(s.text for s in segments).strip()
                finally:
                    os.unlink(tmp_path)
                    if os.path.exists(wav_path):
                        os.unlink(wav_path)
                if text:
                    await websocket.send_json({"type": "transcript", "text": text})
                    await process_text(text, use_tts=True)
                else:
                    await websocket.send_json({"type": "message", "text": "Ich habe dich nicht verstanden, Kevin."})

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
