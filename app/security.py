"""Защитные мелочи сервера: ограничитель частоты запросов, заголовки безопасности, проверка имён.

Ограничитель хранится в памяти процесса: этого достаточно для одного экземпляра uvicorn.
Если запускаете несколько воркеров (--workers N), лимиты считаются отдельно в каждом — вынесите их в Redis/nginx.
"""
import re
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Скользящее окно: не больше `limit` событий за `window` секунд на ключ."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q, now):
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, key) -> bool:
        """Исчерпан ли лимит (событие НЕ записывается)."""
        now = time.time()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                return False
            self._trim(q, now)
            if not q:
                del self._hits[key]
                return False
            return len(q) >= self.limit

    def hit(self, key) -> bool:
        """Записывает событие. Возвращает False, если лимит уже был исчерпан (событие не записано)."""
        now = time.time()
        with self._lock:
            if len(self._hits) > 20000:  # защита памяти от перебора ключей
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
                    del self._hits[k]
            q = self._hits[key]
            self._trim(q, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def retry_after(self, key) -> int:
        now = time.time()
        with self._lock:
            q = self._hits.get(key)
            return max(1, int(self.window - (now - q[0])) + 1) if q else 1


# Имя/фамилия: буквы любого алфавита, внутри допускаются пробел, дефис, апостроф («Анна-Мария», «О’Нил»)
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ \-'’][^\W\d_]+)*$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def clean_name(value: str):
    """Возвращает аккуратное имя или None, если значение не подходит."""
    v = " ".join(str(value or "").split())
    if not (1 <= len(v) <= 50) or not NAME_RE.match(v):
        return None
    return v[0].upper() + v[1:]


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://unpkg.com",
    "connect-src 'self' https://routing.openstreetmap.de https://router.project-osrm.org",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def security_headers(secure: bool) -> dict:
    h = {
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    }
    if secure:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return h
