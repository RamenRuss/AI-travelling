"""РЕАЛЬНЫЙ инструмент find_places: подборка мест рядом с городом по категории (OpenStreetMap, Overpass API).

Дополняет search_places (поиск по названию): «что посмотреть у Находки», «пляжи рядом с Лазо», «кафе в Артёме».
Ключей не требует. Центр города берётся из справочника cities.csv, иначе — геокодер Nominatim (только Приморье).
"""
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import mock_data
from ._ctx import DEFAULT_BEACH_LIMIT, chosen_beaches, claim_beaches, register_place
from ._http import disk_get, disk_put, http_json

log = logging.getLogger("tools")

# Публичные Overpass-серверы часто перегружены, поэтому опрашиваем несколько зеркал ОДНОВРЕМЕННО и берём первый ответ.
# Свой список: OVERPASS_URLS=https://a/api/interpreter,https://b/api/interpreter
OVERPASS_SERVERS = tuple(u.strip() for u in os.environ.get("OVERPASS_URLS", "").split(",") if u.strip()) or (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OVERPASS_TIMEOUT = 12          # сек. на один сервер (раньше 20 × 2 последовательно = до 40 с на каждый вызов)
OVERPASS_COOLDOWN = 90         # сек. не трогаем сервер, который только что не ответил
PLACES_TTL = 7 * 24 * 3600     # места меняются редко — кэш на диске на неделю
_down_until, _down_lock = {}, threading.Lock()
PRIMORYE = (42.2, 130.4, 48.5, 139.1)  # юг, запад, север, восток

CATEGORY_TAGS = {
    "museum": [("tourism", "museum")],
    "attraction": [("tourism", "attraction")],
    "viewpoint": [("tourism", "viewpoint")],
    "beach": [("natural", "beach")],
    "park": [("leisure", "park"), ("boundary", "national_park")],
    "nature": [("natural", "peak"), ("natural", "waterfall"), ("natural", "cape")],
    "monument": [("historic", "monument"), ("historic", "memorial"), ("historic", "fort")],
    "cafe": [("amenity", "cafe"), ("amenity", "restaurant")],
}
RU_NAMES = {"museum": "музей", "attraction": "достопримечательность", "viewpoint": "смотровая", "beach": "пляж",
            "park": "парк", "nature": "природа", "monument": "памятник", "cafe": "кафе"}
ALIASES = {
    "музей": "museum", "достопримечательность": "attraction", "достопримечательности": "attraction",
    "смотровая": "viewpoint", "вид": "viewpoint", "пляж": "beach", "парк": "park", "природа": "nature",
    "гора": "nature", "сопка": "nature", "водопад": "nature", "мыс": "nature", "памятник": "monument",
    "крепость": "monument", "форт": "monument", "кафе": "cafe", "ресторан": "cafe", "еда": "cafe",
}
ALIASES_N = {mock_data.norm(k): v for k, v in ALIASES.items()}
ALIASES_N.update({mock_data.norm(k): v for k, v in {"горы": "nature", "сопки": "nature", "водопады": "nature"}.items()})


def _category(value):
    c = mock_data.norm(value)
    if c in CATEGORY_TAGS:
        return c
    if c in ALIASES_N:
        return ALIASES_N[c]
    # множественное число и падежи: «музеи», «пляжах» -> по первым четырём буквам
    return next((v for k, v in ALIASES_N.items() if len(k) >= 4 and c.startswith(k[:4])), None)


def _center(city):
    c = mock_data.resolve_city(city)
    if c and c["lat"] and c["lon"]:
        return float(c["lat"]), float(c["lon"]), c["city_ru"]
    items = http_json(
        "GET", "https://nominatim.openstreetmap.org/search",
        params={"q": f"{city}, Приморский край", "format": "json", "limit": 1, "accept-language": "ru",
                "viewbox": "130.4,48.5,139.1,42.2", "bounded": 1},
        ttl=30 * 86400, disk=True)
    if items:
        return float(items[0]["lat"]), float(items[0]["lon"]), str(city)
    return None


def _overpass_json(query):
    """Параллельно спрашивает зеркала Overpass. Упавшие сервера на OVERPASS_COOLDOWN секунд исключаются из опроса."""
    now = time.time()
    with _down_lock:
        servers = [u for u in OVERPASS_SERVERS if _down_until.get(u, 0) <= now]
    if not servers:
        raise RuntimeError("все серверы Overpass недавно не отвечали")

    def ask(url):
        return url, http_json("POST", url, data={"data": query}, ttl=86400, retries=0, timeout=OVERPASS_TIMEOUT)

    errors = []
    pool = ThreadPoolExecutor(max_workers=len(servers))
    try:
        futures = {pool.submit(ask, u): u for u in servers}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                return fut.result()[1]
            except Exception as e:  # noqa: BLE001
                with _down_lock:
                    _down_until[url] = time.time() + OVERPASS_COOLDOWN
                errors.append(f"{url.split('/')[2]}: {e}")
                log.warning("Overpass %s не ответил: %s", url, e)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # не ждём медленные сервера, если уже есть ответ
    raise RuntimeError("; ".join(errors))


def _bbox(lat, lon, radius_m):
    d_lat = radius_m / 111_000
    d_lon = radius_m / (111_000 * max(0.2, math.cos(math.radians(lat))))
    return lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon


def _nominatim_fallback(cat, lat, lon, radius_m, limit):
    """Запасной вариант, когда Overpass недоступен: поиск по категории в рамке вокруг города через Nominatim."""
    s, w, n, e = _bbox(lat, lon, radius_m)
    items = http_json(
        "GET", "https://nominatim.openstreetmap.org/search",
        params={"q": RU_NAMES[cat], "format": "jsonv2", "limit": min(limit * 2, 30), "accept-language": "ru",
                "viewbox": f"{w:.4f},{n:.4f},{e:.4f},{s:.4f}", "bounded": 1},
        ttl=86400, disk=True)
    out, seen = [], set()
    for it in items:
        name = it.get("name") or (it.get("display_name") or "").split(",")[0]
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name[:120], "lat": float(it["lat"]), "lon": float(it["lon"])})
    return out


def find_places(ctx, city, category="attraction", limit=8, radius_km=15):
    city = str(city or "").strip()[:100]
    if not city:
        return {"error": "Укажите город или посёлок"}
    cat = _category(category or "attraction")
    if cat is None:
        return {"error": f"Неизвестная категория «{category}». Доступны: {', '.join(RU_NAMES.values())}"}
    try:
        limit = max(1, min(int(limit), 15))
        radius = int(max(1.0, min(float(radius_km), 30.0)) * 1000)  # было до 40 км — запрос становился слишком тяжёлым
    except (TypeError, ValueError):
        return {"error": "limit и radius_km должны быть числами"}

    center = _center(city)
    if not center:
        return {"error": f"Не удалось найти «{city}» в Приморском крае"}
    lat, lon, city_name = center
    if not (PRIMORYE[0] <= lat <= PRIMORYE[2] and PRIMORYE[1] <= lon <= PRIMORYE[3]):
        return {"error": "Ассистент работает только по Приморскому краю"}

    if cat == "beach":
        limit = min(limit, int(ctx.get("beach_limit", DEFAULT_BEACH_LIMIT)))  # столько пляжей просил пользователь (по умолчанию 3)
    cache_key = ("find_places", cat, round(lat, 3), round(lon, 3), radius)
    raw = disk_get(cache_key, PLACES_TTL)
    if raw is None:
        s, w, n, e = _bbox(lat, lon, radius)
        # nwr = node+way+relation одним условием; рамка (bbox) считается заметно быстрее, чем around
        parts = "".join(f'nwr["{k}"="{v}"]["name"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});' for k, v in CATEGORY_TAGS[cat])
        query = f"[out:json][timeout:15];({parts});out center tags 60;"
        try:
            js = _overpass_json(query)
            raw = []
            for el in js.get("elements", []):
                tags = el.get("tags") or {}
                name = tags.get("name:ru") or tags.get("name")
                p_lat = el.get("lat") or (el.get("center") or {}).get("lat")
                p_lon = el.get("lon") or (el.get("center") or {}).get("lon")
                if not name or p_lat is None or p_lon is None:
                    continue
                item = {"name": name[:120], "lat": p_lat, "lon": p_lon}
                if tags.get("opening_hours"):
                    item["hours"] = str(tags["opening_hours"])[:60]
                raw.append(item)
            disk_put(cache_key, raw)
        except RuntimeError as e:
            log.warning("Overpass недоступен (%s), пробуем Nominatim", e)
            try:
                raw = _nominatim_fallback(cat, lat, lon, radius, limit)
            except RuntimeError as e2:
                return {"error": f"Данные о местах сейчас недоступны ({e2}). Не повторяй этот вызов — "
                                 "используй search_places по названию или опиши место без координат."}

    seen, places = set(), []
    for p in sorted(raw, key=lambda p: (p["lat"] - lat) ** 2 + (p["lon"] - lon) ** 2):  # ближе к центру — выше
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        places.append(p)
        if len(places) >= limit:
            break
    dropped = 0
    if cat == "beach":
        # лимит пляжей — на ВЕСЬ ответ: несколько вызовов (разные города) вместе не дадут больше лимита
        places, dropped = claim_beaches(ctx, places)
    # в ответ модели — только то, что нужно для плана; id нужен, чтобы потом указать место в ROUTE
    out_places = []
    for p in places:
        item = {"id": register_place(ctx, p["name"], p["lat"], p["lon"], "beach" if cat == "beach" else None), "name": p["name"],
                "lat": round(p["lat"], 4), "lon": round(p["lon"], 4)}
        if p.get("hours"):
            item["hours"] = p["hours"]
        out_places.append(item)
    out = {"city": city_name, "category": RU_NAMES[cat], "places": out_places}
    if dropped and not out_places:
        out["note"] = (f"Лимит пляжей в ответе исчерпан ({ctx.get('beach_limit', DEFAULT_BEACH_LIMIT)}), "
                       f"уже подобраны: {', '.join(chosen_beaches(ctx))}. Других пляжей не предлагай.")
    elif not out_places:
        out["note"] = "Рядом ничего не найдено — попробуйте другую категорию или больший радиус."
    return out


TOOLS = [{
    "type": "function",
    "function": {
        "name": "find_places",
        "description": "Подборка мест рядом с городом по категории (музеи, пляжи, смотровые, парки, природа, памятники, кафе). "
                       "Пляжей во всём ответе — не больше, чем просил пользователь (по умолчанию 3). Для «что посмотреть в…». Конкретное место по названию — search_places.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Город или посёлок Приморья, например «Находка»"},
                "category": {"type": "string", "enum": list(CATEGORY_TAGS), "description": "Категория мест"},
                "limit": {"type": "integer", "description": "1-15, по умолчанию 8"},
                "radius_km": {"type": "number", "description": "Радиус, км (1-30, по умолчанию 15)"},
            },
            "required": ["city"],
        },
    },
}]

TOOL_FUNCS = {"find_places": find_places}
STEP_LABELS = {"find_places": "Подбираю места рядом"}
