import requests
from config import GROQ_API_KEY, GROQ_MODEL

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def chat(messages: list[dict]) -> str:
    resp = requests.post(GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024
        },
        timeout=30
    )
    return resp.json()["choices"][0]["message"]["content"].strip()

def raw_chat(prompt: str) -> str:
    resp = requests.post(GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 50
        },
        timeout=30
    )
    return resp.json()["choices"][0]["message"]["content"].strip()
