"""«Бухта» — ассистент для путешествий по Приморскому краю. Единая точка входа FastAPI.

Статика (фронтенд), регистрация/вход (SQLite, cookie-сессии), сохранение чатов пользователя
и POST /api/chat. Ответ чата даёт app/agent: настоящий агент (AGENT_MODE=real, по умолчанию) или МОК (для тестов).

Запуск из корня проекта:  uvicorn app.main:app --port 5000
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, config, db
from .security import EMAIL_RE, RateLimiter, clean_name, security_headers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bukhta")

STATIC = Path(__file__).parent / "static"
COOKIE = "session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY = 1_200_000
MIN_PASSWORD, MAX_PASSWORD = 8, 128

# Ограничители частоты (в памяти процесса, см. app/security.py)
login_fail_by_key = RateLimiter(limit=6, window=15 * 60)    # 6 неудачных входов на пару IP+email за 15 минут
login_fail_by_ip = RateLimiter(limit=40, window=15 * 60)    # и 40 на один IP
register_by_ip = RateLimiter(limit=10, window=3600)         # 10 регистраций в час с одного IP
chat_per_minute = RateLimiter(limit=config.CHAT_PER_MINUTE, window=60)
chat_per_day = RateLimiter(limit=config.CHAT_PER_DAY, window=24 * 3600)
state_writes = RateLimiter(limit=120, window=60)
route_calls = RateLimiter(limit=60, window=60)               # /api/route: сервер ходит во внешние сервисы за дорогой

_busy_users, _busy_lock = set(), threading.Lock()  # у одного пользователя не больше одного запроса к модели одновременно


@asynccontextmanager
async def lifespan(_):
    db.init()
    if config.AGENT_MODE == "real" and not config.API_KEY:
        log.warning("AGENT_MODE=real, но SPIRIT_API_KEY не задан — чат будет отвечать ошибкой. "
                    "Задайте ключ (Windows: set SPIRIT_API_KEY=...) и перезапустите сервер.")
    log.info("Агент: %s, модель: %s, инструменты: %s", config.AGENT_MODE, config.MODEL, ", ".join(agent_tools()))
    yield


def agent_tools():
    try:
        from .tools import registry
        return sorted(registry.TOOL_FUNCS)
    except Exception:  # noqa: BLE001
        return []


# /docs и /openapi.json раскрывают устройство API — включаем только при DEBUG=1
app = FastAPI(
    title="Бухта — ассистент Приморского края", lifespan=lifespan,
    docs_url="/docs" if config.DEBUG else None, redoc_url=None,
    openapi_url="/openapi.json" if config.DEBUG else None,
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_HEADERS = security_headers(config.COOKIE_SECURE)


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method in UNSAFE_METHODS:
        # защита от CSRF: запросы с чужого сайта браузер помечает чужим Origin
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
    response = await call_next(request)
    for k, v in _HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


class RegisterReq(BaseModel):
    name: str = Field(max_length=100)
    surname: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=MAX_PASSWORD)


class LoginReq(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=MAX_PASSWORD)


class HistoryItem(BaseModel):
    role: str = Field(max_length=20)
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    message: str = Field(max_length=20000)
    transport: str = Field(default="any", max_length=10)  # any | walk | bike | bus | car
    history: list[HistoryItem] = []


class RoutePoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str = Field(default="", max_length=120)


class RouteReq(BaseModel):
    points: list[RoutePoint] = Field(min_length=2, max_length=15)
    mode: str = Field(default="car", max_length=10)   # car | bike | walk


class StateReq(BaseModel):
    data: dict


def public_user(u) -> dict:
    return {"id": u["id"], "name": u["name"], "surname": u["surname"], "email": u["email"]}


def current_user(session: Optional[str] = Cookie(default=None)):
    u = db.user_by_token(session) if session else None
    if not u:
        raise HTTPException(401, "Нужно войти")
    return public_user(u)


def _login(resp: Response, user_id: int):
    resp.set_cookie(COOKIE, db.create_session(user_id), httponly=True, samesite="lax",
                    secure=config.COOKIE_SECURE, max_age=db.SESSION_TTL, path="/")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon")


@app.post("/api/auth/register")
def register(req: RegisterReq, request: Request, resp: Response):
    if not register_by_ip.hit(client_ip(request)):
        raise HTTPException(429, "Слишком много регистраций, попробуйте позже")
    name, surname = clean_name(req.name), clean_name(req.surname)
    email = req.email.strip().lower()
    if not name:
        raise HTTPException(400, "Введите имя (только буквы, до 50 символов)")
    if not surname:
        raise HTTPException(400, "Введите фамилию (только буквы, до 50 символов)")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "Некорректный email")
    if not (MIN_PASSWORD <= len(req.password) <= MAX_PASSWORD):
        raise HTTPException(400, f"Пароль — от {MIN_PASSWORD} до {MAX_PASSWORD} символов")
    uid = db.create_user(email, name, surname, req.password)
    if uid is None:
        raise HTTPException(409, "Пользователь с таким email уже есть")
    _login(resp, uid)
    return {"user": {"id": uid, "name": name, "surname": surname, "email": email}}


@app.post("/api/auth/login")
def login(req: LoginReq, request: Request, resp: Response):
    ip, email = client_ip(request), req.email.strip().lower()[:254]
    key = f"{ip}|{email}"
    # лимит проверяем ДО хэширования пароля, чтобы перебор не мог ещё и грузить процессор
    if login_fail_by_key.blocked(key) or login_fail_by_ip.blocked(ip):
        raise HTTPException(429, "Слишком много неудачных попыток входа. Подождите 15 минут.")
    u = db.check_login(email, req.password)
    if not u:
        login_fail_by_key.hit(key)
        login_fail_by_ip.hit(ip)
        raise HTTPException(401, "Неверный email или пароль")
    login_fail_by_key.reset(key)
    _login(resp, u["id"])
    return {"user": public_user(u)}


@app.post("/api/auth/logout")
def logout(resp: Response, session: Optional[str] = Cookie(default=None)):
    if session:
        db.drop_session(session)
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


@app.get("/api/state")
def get_state(user: dict = Depends(current_user)):
    return {"data": db.get_state(user["id"])}


@app.put("/api/state")
def put_state(req: StateReq, user: dict = Depends(current_user)):
    if not state_writes.hit(user["id"]):
        raise HTTPException(429, "Слишком часто")
    chats = req.data.get("chats")
    if chats is not None and (not isinstance(chats, list) or len(chats) > 200):
        raise HTTPException(400, "Некорректная история чатов")
    try:
        db.put_state(user["id"], req.data)
    except ValueError as e:
        raise HTTPException(413, str(e))
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"status": "ok", "agent_mode": config.AGENT_MODE}


def clean_history(items):
    """История с клиента: только user/assistant, не длиннее лимитов; system и tool с клиента не пускаем."""
    out = []
    for it in items[-config.MAX_HISTORY_MESSAGES:]:
        if it.role in ("user", "assistant") and it.content.strip():
            out.append({"role": it.role, "content": it.content.strip()[:config.MAX_MESSAGE_CHARS]})
    while out and out[0]["role"] != "user":  # диалог начинается с реплики пользователя
        out.pop(0)
    return out


ROUTE_BOX = (41.5, 49.5, 129.5, 140.0)   # lat_min, lat_max, lon_min, lon_max: проект про Приморье, это не открытый прокси


@app.post("/api/route")
async def api_route(req: RouteReq, user: dict = Depends(current_user)):
    """Дорога по порядку через точки. Отдаёт участки {ok, coords [[lon, lat], …], distance_km, duration_min, note, reason}."""
    for p in req.points:
        if not (ROUTE_BOX[0] <= p.lat <= ROUTE_BOX[1] and ROUTE_BOX[2] <= p.lon <= ROUTE_BOX[3]):
            raise HTTPException(400, "Маршруты строятся только по Приморскому краю и окрестностям")
    if route_calls.blocked(user["id"]):
        raise HTTPException(429, "Слишком много запросов маршрута, повторите через минуту")
    route_calls.hit(user["id"])
    from .tools import _roads
    pts = [{"name": p.name or f"Точка {i + 1}", "lat": p.lat, "lon": p.lon} for i, p in enumerate(req.points)]
    r = await run_in_threadpool(_roads.route_through, pts, req.mode if req.mode in ("car", "bike", "walk") else "car")
    return {"legs": r["legs"], "distance_km": r["distance_km"], "duration_min": r["duration_min"], "failed": r["failed"]}


@app.post("/api/chat")
async def chat(req: ChatRequest, user: dict = Depends(current_user)):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Введите запрос")
    if len(message) > config.MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"Сообщение слишком длинное (максимум {config.MAX_MESSAGE_CHARS} символов)")
    uid = user["id"]
    if chat_per_minute.blocked(uid):
        raise HTTPException(429, f"Слишком много запросов. Повторите через {chat_per_minute.retry_after(uid)} с.")
    if chat_per_day.blocked(uid):
        raise HTTPException(429, "Дневной лимит запросов исчерпан. Попробуйте завтра.")
    with _busy_lock:
        if uid in _busy_users:
            raise HTTPException(429, "Дождитесь ответа на предыдущий вопрос")
        _busy_users.add(uid)
    chat_per_minute.hit(uid)
    chat_per_day.hit(uid)
    try:
        # агент синхронный (сеть, LLM) — выносим в поток, чтобы не блокировать сервер
        return await run_in_threadpool(agent.respond, message, req.transport, clean_history(req.history))
    except agent.LLMError as e:
        raise HTTPException(502, str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Сбой агента")
        raise HTTPException(500, "Внутренняя ошибка сервера. Попробуйте ещё раз.")
    finally:
        with _busy_lock:
            _busy_users.discard(uid)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=5000)
