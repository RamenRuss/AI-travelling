"""Единый реестр инструментов агента. Агент работает ТОЛЬКО через него.

Как добавить инструмент — docs/ADD_TOOL.md. Коротко: положите файл app/tools/<имя>.py, в котором есть
    TOOLS       — список схем для модели (формат OpenAI tool calling)
    TOOL_FUNCS  — {"имя_тула": функция}; первый аргумент функции — ctx
    STEP_LABELS — {"имя_тула": "Подпись шага для интерфейса"}  (необязательно)
Реестр найдёт модуль сам, больше ничего править не нужно. Файлы, имя которых начинается с «_», пропускаются
(там лежит шаблон _template.py).

Сейчас подключено:
    real.py      — search_places, get_weather, get_route (РЕАЛЬНЫЕ внешние API)
    osm_places.py — find_places: подборка мест рядом с городом по категории (OpenStreetMap Overpass)
    mock_data.py — find_fuel, find_lodging, find_transport (МОК: демо-данные из app/data/mock)

Отключить инструмент без правки кода: DISABLED_TOOLS=find_fuel,find_lodging в .env
"""
import importlib
import inspect
import json
import logging
import os
import pkgutil

from .. import config
from . import _ctx

log = logging.getLogger("tools")
_SKIP = {"registry"}


def _load_modules():
    mods = []
    for info in sorted(pkgutil.iter_modules([os.path.dirname(__file__)]), key=lambda m: m.name):
        if info.name in _SKIP or info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__package__}.{info.name}")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Не удалось загрузить модуль инструментов app/tools/{info.name}.py: {e!r}") from e
        if hasattr(mod, "TOOLS") and hasattr(mod, "TOOL_FUNCS"):
            mods.append(mod)
    return mods


def _check_module(mod):
    """Ловим типичные ошибки сразу при старте, с понятным текстом."""
    where = f"app/tools/{mod.__name__.rsplit('.', 1)[-1]}.py"
    names = []
    for t in mod.TOOLS:
        fn = t.get("function", {}) if isinstance(t, dict) else {}
        if t.get("type") != "function" or not fn.get("name") or not fn.get("description") or "parameters" not in fn:
            raise RuntimeError(f"{where}: схема тула должна быть {{'type': 'function', 'function': {{name, description, parameters}}}}")
        names.append(fn["name"])
    if set(names) != set(mod.TOOL_FUNCS):
        raise RuntimeError(f"{where}: имена в TOOLS и TOOL_FUNCS не совпадают: {sorted(set(names) ^ set(mod.TOOL_FUNCS))}")
    for name, func in mod.TOOL_FUNCS.items():
        params = list(inspect.signature(func).parameters)
        if not params or params[0] != "ctx":
            raise RuntimeError(f"{where}: функция {name}() должна принимать ctx первым аргументом: def {name}(ctx, ...)")
        schema = next(t for t in mod.TOOLS if t["function"]["name"] == name)["function"]["parameters"]
        unknown = set(schema.get("properties", {})) - set(params)
        if unknown:
            raise RuntimeError(f"{where}: в схеме {name} есть параметры, которых нет у функции: {sorted(unknown)}")
        required_missing = set(schema.get("required", [])) - set(params)
        if required_missing:
            raise RuntimeError(f"{where}: в required схемы {name} есть параметры, которых нет у функции: {sorted(required_missing)}")


def _build():
    schemas, funcs, labels, sources, declared = [], {}, {}, {}, {}
    for mod in _load_modules():
        _check_module(mod)
        for t in mod.TOOLS:
            name = t["function"]["name"]
            if name in declared:
                raise RuntimeError(f"Тул «{name}» объявлен дважды: {declared[name]} и {mod.__name__}")
            declared[name] = mod.__name__
            if name in config.DISABLED_TOOLS:
                continue
            schemas.append(t)
            funcs[name] = mod.TOOL_FUNCS[name]
            sources[name] = mod.__name__
            labels[name] = getattr(mod, "STEP_LABELS", {}).get(name, f"Вызываю инструмент {name}")
    return schemas, funcs, labels, sources


TOOLS, TOOL_FUNCS, STEP_LABELS, SOURCES = _build()


# ----------------------------------------------------------------------------- контекст запроса и карта

def new_context(beach_limit=_ctx.DEFAULT_BEACH_LIMIT):
    """Контекст одного запроса: найденные места с id (см. _ctx.py). На карту попадают только точки из строки ROUTE.
    beach_limit — сколько пляжей разрешено показать во всём ответе (его считают сами инструменты)."""
    return _ctx.new_context(beach_limit)


split_route = _ctx.split_route
parse_beach_request = _ctx.parse_beach_request
build_map_payload = _ctx.build_map_payload


def step_label(name):
    """Подпись шага для интерфейса («Ход работы агента»)."""
    return STEP_LABELS.get(name, f"Вызываю инструмент {name}")


def run_tool(name, raw_args, ctx):
    """Выполняет инструмент, возвращает результат СТРОКОЙ JSON для модели. Никогда не падает."""
    args = {}
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("аргументы инструмента должны быть JSON-объектом")
        if name not in TOOL_FUNCS:
            result = {"error": f"Неизвестный инструмент «{name}». Доступны: {', '.join(TOOL_FUNCS)}"}
        else:
            result = TOOL_FUNCS[name](ctx, **args)
    except TypeError as e:
        result = {"error": f"Неверные аргументы для {name}: {e}"}
    except Exception as e:  # noqa: BLE001
        result = {"error": f"{type(e).__name__}: {e}"}
    log.info("tool %s %s -> %s", name, args, str(result)[:150])
    return json.dumps(result, ensure_ascii=False, default=str)
