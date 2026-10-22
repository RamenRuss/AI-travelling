"""SQLite: пользователи, сессии, состояние чатов пользователя. Только стандартная библиотека.

Что сделано для безопасности:
  * все запросы параметризованы (`?`), SQL из строк пользователя нигде не собирается;
  * пароли — PBKDF2-HMAC-SHA256 с индивидуальной солью, число итераций хранится в строке пользователя
    и автоматически повышается при входе, если стало устаревшим;
  * в таблице sessions лежит только SHA-256 от токена: утечка файла БД не даёт войти в чужой аккаунт;
  * вход не выдаёт, существует ли email (время ответа одинаковое);
  * соединения всегда закрываются, включён WAL и busy_timeout (нет «database is locked» под нагрузкой);
  * файл БД получает права 600 (на Windows chmod игнорируется — храните БД вне общих папок).
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = os.environ.get("BUKHTA_DB", str(Path(__file__).resolve().parent.parent / "bukhta.db"))
SESSION_TTL = 30 * 24 * 3600
PBKDF2_ITERS = 600_000          # рекомендация OWASP для PBKDF2-HMAC-SHA256
SCHEMA_VERSION = 3
MAX_STATE_BYTES = 1_000_000     # потолок на «состояние чатов» одного пользователя


@contextmanager
def conn():
    """Соединение с коммитом при успехе, откатом при ошибке и ГАРАНТИРОВАННЫМ закрытием."""
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}  # table — константа из кода, не ввод


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            salt TEXT NOT NULL,
            pw_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_state(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            data TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);
        """)
        # ---- миграции старой БД (создана прежней версией приложения) ----
        cols = _columns(c, "users")
        if "surname" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN surname TEXT NOT NULL DEFAULT ''")
        if "iters" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN iters INTEGER NOT NULL DEFAULT 200000")  # так хэшировала старая версия
        if c.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            c.execute("DELETE FROM sessions")  # раньше токены лежали открытым текстом — все старые сессии аннулируем
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


# ----------------------------------------------------------------------------- пароли

def _hash(password: str, salt: bytes, iters: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters).hex()


_DUMMY = None


def _dummy_check(password: str):
    """Тратим столько же времени, сколько на настоящий пароль, чтобы нельзя было узнать по задержке, есть ли email."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = (secrets.token_bytes(16), PBKDF2_ITERS)
    _hash(password, *_DUMMY)


def create_user(email: str, name: str, surname: str, password: str) -> Optional[int]:
    salt = secrets.token_bytes(16)
    try:
        with conn() as c:
            cur = c.execute(
                "INSERT INTO users(email,name,surname,salt,pw_hash,iters,created_at) VALUES(?,?,?,?,?,?,?)",
                (email, name, surname, salt.hex(), _hash(password, salt, PBKDF2_ITERS), PBKDF2_ITERS, int(time.time())),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def check_login(email: str, password: str) -> Optional[sqlite3.Row]:
    with conn() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u:
        _dummy_check(password)
        return None
    iters = u["iters"]
    if not hmac.compare_digest(_hash(password, bytes.fromhex(u["salt"]), iters), u["pw_hash"]):
        return None
    if iters < PBKDF2_ITERS:  # пароль верный, а хэш устарел — тихо перехэшируем
        salt = secrets.token_bytes(16)
        with conn() as c:
            c.execute("UPDATE users SET salt=?, pw_hash=?, iters=? WHERE id=?",
                      (salt.hex(), _hash(password, salt, PBKDF2_ITERS), PBKDF2_ITERS, u["id"]))
    return u


# ----------------------------------------------------------------------------- сессии

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
        c.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)",
                  (_token_hash(token), user_id, now + SESSION_TTL))
    return token  # наружу (в cookie) уходит сам токен, в БД остаётся только его хэш


def user_by_token(token: str):
    if not token or len(token) > 200:
        return None
    with conn() as c:
        return c.execute(
            "SELECT u.id,u.email,u.name,u.surname FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=? AND s.expires_at>?", (_token_hash(token), int(time.time()))).fetchone()


def drop_session(token: str):
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (_token_hash(token),))


# ----------------------------------------------------------------------------- состояние чатов

def get_state(user_id: int):
    with conn() as c:
        r = c.execute("SELECT data FROM user_state WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return None
    try:
        return json.loads(r["data"])
    except ValueError:
        return None


def put_state(user_id: int, data: dict):
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("Слишком большая история чатов")
    with conn() as c:
        c.execute(
            "INSERT INTO user_state(user_id,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, raw, int(time.time())))
