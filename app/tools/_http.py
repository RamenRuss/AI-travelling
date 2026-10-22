"""HTTP-помощник для инструментов: кэш (в памяти и на диске), повторы при сбоях, единый User-Agent.
Файл начинается с «_», поэтому реестр инструментов его не загружает."""
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("tools")

HEADERS = {"User-Agent": "bukhta-primorye-assistant/1.0"}
_cache, _lock = {}, threading.Lock()
_CACHE_MAX = 500
CACHE_DIR = Path(os.environ.get("BUKHTA_CACHE", Path(__file__).resolve().parent.parent / ".cache"))


# ---------- кэш на диске: переживает перезапуск сервера (для редко меняющихся данных: места, геокодер) ----------

def _disk_path(key):
    return CACHE_DIR / (hashlib.sha1(repr(key).encode("utf-8")).hexdigest() + ".json")


def disk_get(key, ttl):
    try:
        p = _disk_path(key)
        if p.exists() and time.time() - p.stat().st_mtime < ttl:
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def disk_put(key, value):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _disk_path(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError):
        pass  # кэш — необязательная оптимизация, ошибки записи не должны ломать запрос


def http_json(method, url, *, params=None, data=None, json_body=None, headers=None, ttl=600, retries=2, timeout=45,
              disk=False):
    """GET/POST, ответ — JSON. Одинаковые запросы в течение ttl секунд берутся из кэша (disk=True — ещё и с диска)."""
    key = (method, url, tuple(sorted((params or {}).items())), tuple(sorted((data or {}).items())),
           repr(json_body) if json_body is not None else None)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    if disk:
        cached = disk_get(key, ttl)
        if cached is not None:
            return cached

    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, params=params, data=data, json=json_body,
                                 headers={**HEADERS, **(headers or {})}, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                last = f"HTTP {r.status_code}"
            else:
                r.raise_for_status()
                try:
                    js = r.json()
                except ValueError:
                    # Overpass и прокси иногда отвечают HTML-страницей с кодом 200
                    raise RuntimeError(f"сервис вернул не JSON (Content-Type={r.headers.get('Content-Type')}, "
                                       f"начало ответа: {r.text[:120]!r})") from None
                with _lock:
                    if len(_cache) >= _CACHE_MAX:
                        _cache.clear()
                    _cache[key] = (time.time(), js)
                if disk:
                    disk_put(key, js)
                return js
        except requests.RequestException as e:
            last = type(e).__name__
            if attempt >= retries:
                break
        log.warning("HTTP %s попытка %d не удалась: %s", url, attempt + 1, last)
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"сервис недоступен ({last})")
