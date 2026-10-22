"""ШАБЛОН инструмента. Скопируйте файл в app/tools/<имя>.py (без «_» в начале) и отредактируйте.
Подробно: docs/ADD_TOOL.md. Этот файл реестр пропускает, потому что имя начинается с «_».
"""
import json  # noqa: F401  (пример: если инструмент ходит во внешний API)


def get_weather(ctx, city, days=3):
    """Пример: погода. ctx — контекст запроса (dict), первый аргумент обязателен.

    Правила:
      * возвращайте словарь (он станет JSON для модели);
      * при проблеме возвращайте {"error": "понятный текст"}, а не бросайте исключение;
      * хотите, чтобы место можно было вывести на карту — верните его с id: from ._ctx import register_place; {"id": register_place(ctx, name, lat, lon), ...}. На карте окажутся только те id, которые модель укажет в строке ROUTE.
    """
    # TODO: вызов вашего погодного API, например requests.get(..., timeout=10)
    return {"error": "get_weather ещё не реализован"}


TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Прогноз погоды в городе Приморья. Используй, когда спрашивают про погоду или что надеть.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Город, например «Находка»"},
                "days": {"type": "integer", "description": "На сколько дней (по умолчанию 3)"},
            },
            "required": ["city"],
        },
    },
}]

TOOL_FUNCS = {"get_weather": get_weather}

STEP_LABELS = {"get_weather": "Запрашиваю прогноз погоды"}
