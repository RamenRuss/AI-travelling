"""Контракт ответа агента фронтенду и страховка от кривых ответов.

Фронтенд (render.js / app.js) падает, если в ответе нет нужных полей, поэтому ответы настоящего и своего
агента проходят через finalize(): он добавляет недостающее безопасными значениями.

Поля ответа:
  type   — "info" | "plan" | "refusal"
  title, text — заголовок и текст
  steps  — список строк для блока «Ход работы агента» (обязательно, иначе чат ломается)
  meta   — {"tools": число, "tokens": число, "seconds": число}
  info:    bullets (список строк), followup (строка) — могут быть пустыми
  plan:    summary, intents, weather, days, overnight, notes, checks — формат в app/agent/mock_agent.py
  любой:   map — данные карты {markers, route} (необязательно)
"""
TYPES = ("info", "plan", "refusal")
PLAN_KEYS = ("title", "summary", "intents", "weather", "days", "notes", "checks")


def finalize(data, tools_used=0, seconds=0.0):
    """Приводит ответ агента к виду, который не ломает интерфейс."""
    d = dict(data) if isinstance(data, dict) else {"text": str(data)}
    if d.get("type") not in TYPES:
        d["type"] = "info"
    if d["type"] == "plan" and not all(isinstance(d.get(k), list if k in ("intents", "weather", "days", "notes", "checks") else str)
                                       for k in PLAN_KEYS):
        d["type"] = "info"  # неполный план фронтенд не нарисует — показываем как текст
    d["title"] = str(d.get("title") or ("Не могу помочь" if d["type"] == "refusal" else "Ответ"))
    d["text"] = str(d.get("text") or d.get("summary") or "")
    steps = d.get("steps")
    d["steps"] = [str(s) for s in steps] if isinstance(steps, (list, tuple)) and steps else ["Формирую ответ"]
    bullets = d.get("bullets")
    d["bullets"] = [str(b) for b in bullets] if isinstance(bullets, (list, tuple)) else []
    d["followup"] = str(d.get("followup") or "")
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    meta.setdefault("tools", int(tools_used))
    meta.setdefault("tokens", 0)
    meta.setdefault("seconds", round(float(seconds), 1))
    d["meta"] = meta
    return d


def error_response(title, text, step="Ошибка"):
    return finalize({"type": "refusal", "title": title, "text": text, "steps": [step]})
