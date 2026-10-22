"""Точка входа агента: respond(message, transport) -> dict в формате фронтенда.

AGENT_MODE=real   (по умолчанию) — встроенный агент (LLM + инструменты из app/tools/registry.py), нужен SPIRIT_API_KEY.
AGENT_MODE=mock   — МОК-агент для тестов: ничего не требует и работает без интернета.
AGENT_MODE=custom — ваш агент: AGENT=пакет.модуль:функция, функция(message, transport) -> dict.
Инструменты подключаются в app/tools/ (docs/ADD_TOOL.md), агент — здесь (docs/ADD_AGENT.md).
"""
import importlib
import time

from .. import config
from . import contract, mock_agent, real_adapter
from .llm import LLMError  # noqa: F401  (main.py ловит эту ошибку)


def _load_custom(spec):
    module, _, func = spec.partition(":")
    if not module or not func:
        raise LLMError(f"AGENT должен быть в формате пакет.модуль:функция, а там «{spec}»")
    try:
        return getattr(importlib.import_module(module), func)
    except (ImportError, AttributeError) as e:
        raise LLMError(f"Не удалось загрузить агента AGENT={spec}: {e}") from e


def respond(message: str, transport: str = "any", history=None) -> dict:
    if config.AGENT_MODE == "real":
        return real_adapter.respond(message, transport, history)
    if config.AGENT_MODE == "custom":
        started = time.time()
        try:
            data = _load_custom(config.AGENT)(message, transport)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 — ошибка чужого агента не должна ронять сервер
            raise LLMError(f"Агент не ответил: {e}") from e
        return contract.finalize(data, seconds=time.time() - started)
    time.sleep(0.4)  # МОК: имитация задержки агента
    return mock_agent.respond(message, transport)
