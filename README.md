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
cp config.example.py config.py
# config.py mit deinen API Keys befüllen
pip install -r requirements.txt
```

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

## V2 Features (in Entwicklung)
- Blender MCP Steuerung
- Gmail Kategorisierung
- Telegram Voice Interface
- Home Assistant Integration

## Hinweis
`config.py` niemals committen – enthält API Keys!
