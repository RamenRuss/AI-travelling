"""Настройки и ключи. Всё берётся из переменных окружения или из файла .env в корне проекта."""
import os
from pathlib import Path


def _load_dotenv():
    """Мини-загрузчик .env (без зависимостей). Уже заданные переменные окружения не перезаписывает."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

# Какой агент отвечает в чате: "real" (LLM + инструменты, по умолчанию), "mock" (заготовки для тестов) или "custom"
AGENT_MODE = os.environ.get("AGENT_MODE", "real").lower()   # real | mock | custom

# Свой агент (AGENT_MODE=custom): AGENT=пакет.модуль:функция, функция(message, transport) -> dict. См. docs/ADD_AGENT.md
AGENT = os.environ.get("AGENT", "").strip()

# --- Реальный агент ---
API_KEY = os.environ.get("SPIRIT_API_KEY")                 # ключ LLM-прокси
GOOGLE_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")       # необязательно: без него места ищет OSM
ORS_KEY = os.environ.get("ORS_API_KEY")                    # необязательно: без него маршрут через OSRM (только авто)

LLM_URL = os.environ.get("LLM_URL", "https://llm-proxy.spirit.t-education.com/v1/chat/completions")
MODEL = os.environ.get("AGENT_MODEL", "openrouter/deepseek/deepseek-v4.1-flash")
# Потолок ответа модели (раньше 35000: лишний запас, который провайдер может резервировать и тарифицировать)
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "6000") or 6000)

# Отключить инструменты без правки кода (через запятую): DISABLED_TOOLS=find_fuel,find_lodging
DISABLED_TOOLS = {t.strip() for t in os.environ.get("DISABLED_TOOLS", "").split(",") if t.strip()}


# --- Безопасность и лимиты ---
def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Cookie с флагом Secure (нужен при работе по HTTPS; на http://localhost оставьте 0)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
# Включить /docs и /openapi.json (только для разработки)
DEBUG = os.environ.get("DEBUG", "0") == "1"
# Доверять заголовку X-Forwarded-For (только если сервер стоит за своим nginx/прокси!)
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"
# Лимиты запросов к модели на одного пользователя (защита от лишних трат)
CHAT_PER_MINUTE = _int("CHAT_PER_MINUTE", 10)
CHAT_PER_DAY = _int("CHAT_PER_DAY", 300)
MAX_MESSAGE_CHARS = _int("MAX_MESSAGE_CHARS", 1500)
MAX_HISTORY_MESSAGES = _int("MAX_HISTORY_MESSAGES", 12)
