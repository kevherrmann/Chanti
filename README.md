# Chanti – Lokaler KI-Assistent

Chanti ist ein persönlicher KI-Assistent mit Web-UI, Sprachausgabe und Smart-Home-Integration.

## Features
- 🧠 LLM via Groq (llama-3.3-70b) oder lokal (Ollama)
- 🔊 Sprachausgabe via XTTS v2 (GPU)
- 🎤 Spracheingabe via Whisper (Browser + Groq)
- 💬 Telegram Bot Integration (Text + Sprachnachrichten)
- 🏠 Home Assistant / Tuya Lampensteuerung
- 🔍 DuckDuckGo Websuche
- 🎨 Blender MCP Integration
- 📧 Gmail Kategorisierung via n8n
- 🌐 Web-Chat-UI

## Setup

### Voraussetzungen
- Python 3.11 (Conda)
- XTTS v2 Environment
- Groq API Key (kostenlos: console.groq.com)
- n8n (npm)
- Home Assistant (Docker)
- ngrok (für Telegram Webhooks)

### Installation

```bash
git clone https://github.com/kevherrmann/Chanti.git
cd Chanti
cp .env.example .env
# .env mit deinen API Keys befüllen
pip install -r requirements.txt
playwright install chromium
```

### Konfiguration

Alle Secrets und Pfade werden über Umgebungsvariablen konfiguriert.
Entweder `.env` Datei nutzen oder Variablen direkt setzen:

```bash
export GROQ_API_KEY=gsk_dein_key
export HA_TOKEN=dein_ha_token
export CHANTI_API_KEY=optionaler_auth_key
```

Alternativ: `config.py` direkt editieren (niemals committen!).

### Starten
```bash
chanti  # oder: ~/chanti/start.sh
```

## Architektur
STT  → Groq Whisper API
LLM  → Groq llama-3.3-70b
TTS  → XTTS v2 (lokal, GPU)
Gedächtnis → lokal (memory.md)
Automatisierungen → n8n

## Sicherheit
- `/chat` und `/notify` Endpoints können mit `CHANTI_API_KEY` geschützt werden
- Chat-UI nutzt DOMPurify gegen XSS
- File-Edit Skill ist auf `~/chanti/` beschränkt

## V2 Features (in Entwicklung)
- Blender MCP Steuerung
- Gmail Kategorisierung
- Telegram Voice Interface
- Home Assistant Integration

## Hinweis
`.env` und `config.py` niemals committen – enthalten API Keys!
