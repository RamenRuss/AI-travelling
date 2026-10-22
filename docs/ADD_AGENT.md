# Как подключить агента

Сервер (`app/main.py`, `POST /api/chat`) вызывает одну функцию: `app.agent.respond(message, transport) -> dict`.
Какой агент ответит, решает `AGENT_MODE` в `.env`.

| `AGENT_MODE` | Агент | Что нужно |
|---|---|---|
| `real` (по умолчанию) | встроенный: `app/agent/llm.py` — модель вызывает все инструменты из реестра, помнит историю чата | `SPIRIT_API_KEY` |
| `mock` | `app/agent/mock_agent.py` — заготовленные ответы (для тестов) | ничего |
| `custom` | ваш агент | `AGENT=пакет.модуль:функция` |

## Вариант 1. Встроенный агент (`real`)
```
AGENT_MODE=real
SPIRIT_API_KEY=ваш_ключ
# AGENT_MODEL=openrouter/deepseek/deepseek-v4.1-flash   # по желанию
```
Агент уже видит **все** инструменты: настоящие (`search_places`, `find_places`, `get_weather`, `get_route`) и по демо-данным
(`find_fuel`, `find_lodging`, `find_transport`). Правила поведения — `SYSTEM_PROMPT` в `app/agent/llm.py`.
Проверка ключа: `python scripts/check_llm.py`.

## Вариант 2. Свой агент (`custom`)
```
AGENT_MODE=custom
AGENT=my_agent.main:respond
```
```python
# my_agent/main.py
from app.tools import registry          # инструменты и схемы уже собраны

def respond(message: str, transport: str = "any") -> dict:
    ctx = registry.new_context()
    # registry.TOOLS                  — схемы инструментов (формат OpenAI; для Anthropic input_schema = parameters)
    # registry.run_tool(name, args, ctx) — результат JSON-строкой, никогда не падает
    # registry.step_label(name)       — подпись шага для блока «Ход работы агента»
    # registry.build_map_payload(ctx) — данные карты {markers, route}
    ...
    return {"type": "info", "title": "Ответ", "text": "...", "steps": ["Разбираю запрос", "Формирую ответ"]}
```
Любую ошибку вашего агента сервер превратит в ответ 502 с текстом, а не упадёт. Недостающие поля ответа
добавит `app/agent/contract.py::finalize` — иначе фронтенд ломается (например, без `steps` или `bullets`).

## Формат ответа
| Поле | Обязательно | Смысл |
|---|---|---|
| `type` | да | `info`, `plan` или `refusal` |
| `title`, `text` | да | заголовок и текст |
| `steps` | да | список строк, блок «Ход работы агента» |
| `meta` | да | `{"tools": число, "tokens": число, "seconds": число}` |
| `bullets`, `followup` | для `info` | список строк и строка (могут быть пустыми) |
| `map` | нет | `{markers, route}`; фронтенд пока рисует карту только для `plan` |

`type: "plan"` — карточка по дням с картой. Нужны поля `title, summary, intents, weather, days, notes, checks`
(пример — `mock_agent.py`: `_plan_route`). Если чего-то не хватает, `finalize` покажет ответ как `info`.
Точки плана: `{time, name, note, lat, lng, status?, water?}` (здесь `lng`, как в mock). Маркеры инструментов:
`{name, lat, lon, note}` (здесь `lon`, как в `real.py`).

## Проверка
```bash
python -m pytest -q                     # без интернета и ключей
python scripts/check_llm.py             # ключ и доступ к модели
python -m eval.run_eval --queries eval/queries.csv --label v1   # прогон запросов через real-агента
```

## Частые проблемы
| Симптом | Причина |
|---|---|
| 502 «Не задан SPIRIT_API_KEY» | нет ключа при `AGENT_MODE=real` |
| 502 «Не удалось загрузить агента» | неверный `AGENT=` или модуль не в `PYTHONPATH` (запускайте из корня проекта) |
| Модель не вызывает инструменты | модель без tool calling; смените `AGENT_MODEL` |
| Модель выдумывает рейсы и цены | усильте `SYSTEM_PROMPT`, проверьте `description` инструментов |
| «Слишком много шагов» | лимит `MAX_TOOL_ROUNDS` в `llm.py` (6) |
