"""Проверка доступа к LLM-прокси: ключ, сеть, модель. Запуск: python scripts/check_llm.py"""
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402  (читает .env и переменные окружения)

API_KEY = config.API_KEY
if not API_KEY:
    raise RuntimeError("Не задан SPIRIT_API_KEY (Windows: set SPIRIT_API_KEY=ваш_ключ)")

URL = config.LLM_URL
MODEL = config.MODEL   # та же модель, что использует агент (AGENT_MODEL в .env)

r = requests.post(
    URL,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Ты туристический AI-помощник, который отвечает только о Приморском крае."},
            {"role": "user", "content": "Назови три места для первой поездки во Владивосток."},
        ],
        "max_tokens": 2000,
    },
    timeout=120,
)

print("HTTP:", r.status_code)
if not r.ok:
    raise SystemExit(f"Ошибка: {r.text[:300]}")
data = r.json()
choice = data["choices"][0]
print("finish_reason:", choice.get("finish_reason"))
print("usage:", data.get("usage"))
print("ОТВЕТ:", choice["message"].get("content"))