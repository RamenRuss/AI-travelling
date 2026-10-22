"""РЕАЛЬНЫЕ инструменты агента: search_places, get_weather, get_route (настоящие внешние API).

Ничего не знает ни про модель, ни про FastAPI. Моков здесь нет.
"""
import logging

import requests

from .. import config
from ._ctx import DEFAULT_BEACH_LIMIT, chosen_beaches, claim_beaches, is_beach_name, register_place
from ._http import http_json
from ._roads import FAR_FROM_ROAD_M, NoRoadRoute, RoutingUnavailable, road_route

log = logging.getLogger("tools")

PRIMORYE_BOX = {
    "low": {"latitude": 42.2, "longitude": 130.4},
    "high": {"latitude": 48.5, "longitude": 139.1},
}


def http_get(url, ttl=600, **kw):
    return http_json("GET", url, params=kw.get("params"), ttl=ttl)


def _coord(value, name, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} должно быть числом") from None
    if not (lo <= v <= hi):
        raise ValueError(f"{name}={v} вне допустимого диапазона {lo}…{hi}")
    return v


def yandex_link(lat, lon):
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map"


# ---------- Места ----------

def _google_places(query, n):
    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": config.GOOGLE_KEY,
            # только нужные поля: от набора полей зависит стоимость запроса
            "X-Goog-FieldMask": (
                "places.displayName,places.formattedAddress,places.location,"
                "places.rating,places.userRatingCount,places.editorialSummary"
            ),
        },
        json={
            "textQuery": query,
            "languageCode": "ru",
            "pageSize": n,
            "locationRestriction": {"rectangle": PRIMORYE_BOX},
        },
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Google Places {r.status_code}: {r.text[:300]}")
    out = []
    for p in r.json().get("places", []):
        out.append({
            "name": p["displayName"]["text"],
            "address": p.get("formattedAddress"),
            "lat": p["location"]["latitude"],
            "lon": p["location"]["longitude"],
            "rating": p.get("rating"),
            "reviews": p.get("userRatingCount"),
            "summary": (p.get("editorialSummary") or {}).get("text"),
        })
    return out


def _osm_places(query, n):
    items = http_json(
        "GET", "https://nominatim.openstreetmap.org/search",
        params={
            "q": query, "format": "jsonv2", "limit": n,
            "accept-language": "ru", "countrycodes": "ru",
            "viewbox": "130.4,48.5,139.1,42.2", "bounded": 1,
        },
        ttl=3600,
    )
    return [{
        "name": it.get("name") or it["display_name"].split(",")[0],
        "address": it["display_name"],
        "lat": float(it["lat"]),
        "lon": float(it["lon"]),
    } for it in items]


def _short_address(addr):
    """Полный адрес Nominatim очень длинный (регион, индекс, страна) — модели достаточно первых частей."""
    parts = [x.strip() for x in str(addr or "").split(",")]
    return ", ".join(parts[:3])[:90]


def search_places(ctx, query, max_results=5):
    query = str(query or "").strip()[:200]
    if not query:
        return {"error": "Пустой запрос для поиска мест"}
    n = max(1, min(int(max_results), 8))
    places = None
    if config.GOOGLE_KEY:
        try:
            places = _google_places(query, n)
        except Exception as e:  # noqa: BLE001 — при сбое Google не оставляем модель без данных
            log.warning("Google Places недоступен, пробуем OpenStreetMap: %s", type(e).__name__)
    if places is None:
        places = _osm_places(query, n)
    # пляжи из поиска по названию тоже считаются в общий лимит пляжей ответа (иначе лимит find_places обходится)
    beaches = [p for p in places if is_beach_name(p["name"])]
    allowed, dropped = claim_beaches(ctx, beaches) if beaches else ([], 0)
    allowed_ids = {id(p) for p in allowed}
    places = [p for p in places if not is_beach_name(p["name"]) or id(p) in allowed_ids]
    out = []
    for p in places:
        item = {"id": register_place(ctx, p["name"], p["lat"], p["lon"], "beach" if is_beach_name(p["name"]) else None),
                "name": p["name"],
                "addr": _short_address(p.get("address")), "lat": round(p["lat"], 4), "lon": round(p["lon"], 4)}
        if p.get("rating"):
            item["rating"] = p["rating"]
        if p.get("summary"):
            item["info"] = str(p["summary"])[:160]
        out.append(item)
    res = {"places": out}
    if dropped:
        res["note"] = (f"Часть пляжей убрана: лимит пляжей в ответе — {ctx.get('beach_limit', DEFAULT_BEACH_LIMIT)}, "
                       f"уже подобраны: {', '.join(chosen_beaches(ctx))}. Других пляжей не предлагай.")
    return res


# ---------- Погода ----------

def _wmo(code):
    if code == 0: return "ясно"
    if code in (1, 2): return "малооблачно"
    if code == 3: return "пасмурно"
    if code in (45, 48): return "туман"
    if 51 <= code <= 57: return "морось"
    if 61 <= code <= 67: return "дождь"
    if 71 <= code <= 77: return "снег"
    if 80 <= code <= 82: return "ливень"
    if code in (85, 86): return "снегопад"
    if code >= 95: return "гроза"
    return "без описания"


def get_weather(ctx, city="", lat=None, lon=None, days=3):
    days = max(1, min(int(days), 7))
    city = str(city or "").strip()[:100]
    place = city
    if lat is None or lon is None:
        if not city:
            return {"error": "Нужно указать city или lat и lon"}
        g = http_get(
            "https://geocoding-api.open-meteo.com/v1/search",
            ttl=86400,
            params={"name": city, "count": 10, "language": "ru", "countryCode": "RU"},
        )
        # одноимённых посёлков по России много — берём тот, что внутри Приморского края
        inside = [r for r in g.get("results") or []
                  if 42.2 <= r["latitude"] <= 48.5 and 130.4 <= r["longitude"] <= 139.1]
        if not inside:
            return {"error": f"Населённый пункт «{city}» в Приморском крае не найден"}
        res = inside[0]
        lat, lon, place = res["latitude"], res["longitude"], res["name"]
    else:
        lat, lon = _coord(lat, "lat", -90, 90), _coord(lon, "lon", -180, 180)

    w = http_get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_sum,precipitation_probability_max,wind_speed_10m_max"),
            "timezone": "auto",
            "forecast_days": days,
        },
    )
    d = w["daily"]
    forecast = [{
        "date": day,
        "sky": _wmo(d["weather_code"][i]),
        "t": f'{round(d["temperature_2m_min"][i])}…{round(d["temperature_2m_max"][i])}',
        "rain_mm": d["precipitation_sum"][i],
        "rain_pct": d["precipitation_probability_max"][i],
        "wind_kmh": round(d["wind_speed_10m_max"][i]),
    } for i, day in enumerate(d["time"])]
    return {"place": place, "forecast": forecast}


# ---------- Маршруты ----------

def get_route(ctx, from_lat, from_lon, to_lat, to_lon, mode="car"):
    # у маршрутизаторов порядок: долгота, широта
    a = (_coord(from_lon, "from_lon", -180, 180), _coord(from_lat, "from_lat", -90, 90))
    b = (_coord(to_lon, "to_lon", -180, 180), _coord(to_lat, "to_lat", -90, 90))
    mode = mode if mode in ("car", "walk", "bike") else "car"
    try:
        r = road_route(a, b, mode)
    except NoRoadRoute:
        return {"error": "По дорогам между этими точками не добраться (возможно, нужен паром или катер)"}
    except RoutingUnavailable as e:
        return {"error": f"Сервис маршрутов недоступен: {e}"}
    out = {"distance_km": round(r["distance_m"] / 1000, 1), "duration_min": round(r["duration_s"] / 60), "mode": mode}
    if r["snapped_m"] > FAR_FROM_ROAD_M:
        out["note"] = f"точка в {round(r['snapped_m'] / 1000, 1)} км от ближайшей дороги"
    return out


# ---------- Реестр и схемы для модели ----------

TOOL_FUNCS = {
    "search_places": search_places,
    "get_weather": get_weather,
    "get_route": get_route,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Ищет место в Приморском крае по названию или запросу. Возвращает id, название, координаты.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Например: «маяк Владивосток» или «кафе у набережной»"},
                    "max_results": {"type": "integer", "description": "Сколько мест вернуть (1-8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Прогноз погоды по дням. Укажи city либо lat и lon; days — ровно столько, сколько дней поездки.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "days": {"type": "integer", "description": "1-7"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route",
            "description": "Расстояние и время в пути между двумя точками. Только когда это нужно для плана.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_lat": {"type": "number"},
                    "from_lon": {"type": "number"},
                    "to_lat": {"type": "number"},
                    "to_lon": {"type": "number"},
                    "mode": {"type": "string", "enum": ["car", "walk", "bike"]},
                },
                "required": ["from_lat", "from_lon", "to_lat", "to_lon"],
            },
        },
    },
]


# Подписи шагов в интерфейсе («Ход работы агента»). Агент вызывает тулы через app/tools/registry.py
STEP_LABELS = {
    "search_places": "Ищу места",
    "get_weather": "Запрашиваю прогноз погоды",
    "get_route": "Строю маршрут",
}
