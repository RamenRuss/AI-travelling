"""Переводит ответ настоящего агента (llm.run_chat) в формат, который понимает фронтенд.

run_chat -> {"answer", "tools_used", "cost", "tokens", "map": {"markers", "route"}}
фронтенд  <- {"type": "info", "title", "text", "bullets", "followup", "meta", ...}

Ответ показывается как «info»: текст модели (мини-markdown) и кнопка «Открыть карту», если инструменты
отметили места или маршрут. Карточку плана по дням (type="plan") делает только mock-агент.
"""
import time

from . import contract
from .llm import run_chat

TRANSPORT_RU = {"walk": "пешком", "bike": "на велосипеде", "bus": "автобус и поезд", "car": "на автомобиле"}


def respond(message: str, transport: str = "any", history=None) -> dict:
    """history — предыдущие реплики [{role: user|assistant, content}] (main.py их уже проверил и обрезал)."""
    text = message.strip()
    if transport in TRANSPORT_RU:
        text += f"\n(Транспорт: {TRANSPORT_RU[transport]})"

    started = time.time()
    result = run_chat(list(history or []) + [{"role": "user", "content": text}])

    return contract.finalize({
        "type": "info",
        "title": "Нужно уточнить" if result.get("clarify") else "Ответ",   # вопросы вместо плана — отдельный заголовок
        "text": result["answer"],
        "steps": result.get("steps"),  # без steps фронтенд падает — finalize подставит запасной
        "bullets": [],
        "followup": "",
        "meta": {
            "tools": len(result["tools_used"]),
            "tokens": result.get("tokens", 0),
            "seconds": round(time.time() - started, 1),
        },
        "map": None if result.get("clarify") else result.get("map"),  # фронтенд рисует кнопку «Открыть карту» для info-ответов с map
    })
