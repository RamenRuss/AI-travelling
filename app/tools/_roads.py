"""Построение РЕАЛЬНОГО маршрута по дорогам между двумя точками и через несколько точек.
Файл начинается с «_», поэтому реестр инструментов его не загружает.

Источники: OpenRouteService (если задан ORS_API_KEY; авто, пешком, велосипед) и OSRM (запасной, только авто).
Прямых линий здесь нет никогда: если дороги нет (остров без моста, нет связи с дорожной сетью) или сервис
недоступен, участок помечается как неудавшийся, а не «дорисовывается» по прямой.
"""
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .. import config
from ._http import http_json

log = logging.getLogger("tools")

MAX_POINTS_PER_LEG = 250     # прореживание линии, чтобы ответ с картой не раздувался
FAR_FROM_ROAD_M = 1500       # точка дальше этого от дороги — предупреждаем (маршрут идёт до ближайшей дороги)
SAME_PLACE_KM = 0.05
RETRY_PAUSE_S = 0.8          # пауза перед второй попыткой, если сервисы маршрутов не ответили (сбой, лимит, таймаут)
_ORS_PROFILE = {"car": "driving-car", "walk": "foot-walking", "bike": "cycling-regular"}
_cache, _lock = {}, threading.Lock()


class NoRoadRoute(RuntimeError):
    """Между точками нет пути по дорогам (остров без моста, закрытая территория и т. п.)."""


class RoutingUnavailable(RuntimeError):
    """Сервис маршрутов не ответил или режим не поддерживается."""


def haversine_m(lon1, lat1, lon2, lat2):
    rad = math.radians
    h = math.sin(rad(lat2 - lat1) / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(rad(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _thin(coords, limit=MAX_POINTS_PER_LEG):
    if len(coords) <= limit:
        return coords
    step = (len(coords) - 1) / (limit - 1)
    return [coords[round(i * step)] for i in range(limit)]


def _ors(a, b, mode):
    r = requests.post(
        f"https://api.openrouteservice.org/v2/directions/{_ORS_PROFILE[mode]}/geojson",
        headers={"Authorization": config.ORS_KEY, "Content-Type": "application/json"},
        json={"coordinates": [list(a), list(b)], "language": "ru", "radiuses": [-1, -1]},   # -1: искать дорогу сколь угодно далеко
        timeout=30,
    )
    if r.status_code == 404 or (r.status_code == 400 and "routable point" in r.text.lower()):
        raise NoRoadRoute("ORS: рядом с точкой нет дороги или путь не найден")
    if not r.ok:
        raise RoutingUnavailable(f"OpenRouteService {r.status_code}")
    feat = r.json()["features"][0]
    s = feat["properties"]["summary"]
    return s["distance"], s["duration"], feat["geometry"]["coordinates"], "OpenRouteService"


def _osrm_like(url, name):
    data = http_json("GET", url, params={"overview": "full", "geometries": "geojson", "radiuses": "unlimited;unlimited"}, ttl=86400, timeout=15, retries=1)
    code = data.get("code")
    if code in ("NoRoute", "NoSegment"):
        raise NoRoadRoute(f"{name}: {code}")
    if code != "Ok":
        raise RoutingUnavailable(f"{name}: {code}")
    rt = data["routes"][0]
    return rt["distance"], rt["duration"], rt["geometry"]["coordinates"], name


_FOSSGIS = {"car": "routed-car", "bike": "routed-bike", "walk": "routed-foot"}


def _fossgis(a, b, mode):
    """routing.openstreetmap.de — тот же OSRM, но с отдельными профилями авто/велосипед/пешком; ключ не нужен."""
    return _osrm_like(f"https://routing.openstreetmap.de/{_FOSSGIS[mode]}/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}", "OSRM (openstreetmap.de)")


def _osrm(a, b):
    return _osrm_like(f"https://router.project-osrm.org/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}", "OSRM")


def road_route(a, b, mode="car"):
    """a, b — (lon, lat). Возвращает {coords [[lon, lat], …], distance_m, duration_s, source, snapped_m}.
    Бросает NoRoadRoute (пути по дорогам нет) или RoutingUnavailable (сервис недоступен)."""
    mode = mode if mode in _ORS_PROFILE else "car"
    key = (tuple(round(x, 5) for x in a), tuple(round(x, 5) for x in b), mode, bool(config.ORS_KEY))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]

    # Источники по порядку: ORS (если есть ключ) -> routing.openstreetmap.de (авто/вело/пешком) -> router.project-osrm.org (авто).
    # «Нет пути» от OSRM-сервиса окончательно (дороги не связаны, например остров); от ORS — не окончательно:
    # он мог не найти дорогу рядом с точкой, поэтому пробуем следующий источник.
    sources = []
    if config.ORS_KEY:
        sources.append(lambda: _ors(a, b, mode))
    sources.append(lambda: _fossgis(a, b, mode))
    if mode == "car":
        sources.append(lambda: _osrm(a, b))
    result, errors, no_route_count = None, [], 0
    for i, src in enumerate(sources):
        try:
            result = src()
            break
        except NoRoadRoute as e:
            # У разных маршрутизаторов могут различаться дорожные данные и привязка к дороге.
            # Отказ одного сервиса не должен блокировать попытку через следующий.
            no_route_count += 1
            errors.append(str(e))
            log.warning("Источник маршрута не нашёл путь (%s), пробуем следующий: %s", i + 1, errors[-1])
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")
            log.warning("Источник маршрута недоступен (%s), пробуем следующий: %s", i + 1, errors[-1])
    if result is None:
        if sources and no_route_count == len(sources):
            raise NoRoadRoute("; ".join(errors))
        raise RoutingUnavailable("; ".join(errors) or "нет доступных сервисов маршрутов")

    dist, dur, coords, source = result
    snapped = max(haversine_m(a[0], a[1], coords[0][0], coords[0][1]),
                  haversine_m(b[0], b[1], coords[-1][0], coords[-1][1])) if coords else 0
    out = {
        "coords": [[round(x, 5), round(y, 5)] for x, y in _thin(coords)],
        "distance_m": dist, "duration_s": dur, "source": source, "snapped_m": round(snapped),
    }
    with _lock:
        if len(_cache) > 800:
            _cache.clear()
        _cache[key] = (time.time(), out)
    return out


def route_leg(p, q, mode="car"):
    """Один участок между местами p и q ({name, lat, lon}) — всегда словарь, никогда не исключение.
    Если сервисы не ответили — одна повторная попытка: публичные OSRM изредка отвечают сбоем, а второй раз проходит.
    «Нет дороги» (остров, нет связи с дорожной сетью) не повторяется: это окончательный ответ."""
    leg = {"from": p["name"], "to": q["name"]}
    for attempt in (1, 2):
        try:
            r = road_route((p["lon"], p["lat"]), (q["lon"], q["lat"]), mode)
            break
        except NoRoadRoute:
            return {**leg, "ok": False, "reason": "no_road",
                    "note": "по дорогам не добраться (возможно, нужен паром или катер)"}
        except (RoutingUnavailable, requests.RequestException, KeyError, IndexError, ValueError) as e:
            log.warning("Маршрут %s -> %s не построен (попытка %d): %s", p["name"], q["name"], attempt, e)
            if attempt == 2:
                return {**leg, "ok": False, "reason": "service", "note": "сервис маршрутов не ответил"}
            time.sleep(RETRY_PAUSE_S)
    leg.update(ok=True, coords=r["coords"], distance_km=round(r["distance_m"] / 1000, 1),
               duration_min=round(r["duration_s"] / 60), source=r["source"])
    if r["snapped_m"] > FAR_FROM_ROAD_M:
        leg["note"] = f"точка в {round(r['snapped_m'] / 1000, 1)} км от ближайшей дороги — дальше пешком"
    return leg


def route_legs(pairs, mode="car"):
    """Участки для списка пар мест [(p, q), …] параллельно, в том же порядке."""
    if not pairs:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(pairs))) as pool:
        return list(pool.map(lambda pq: route_leg(pq[0], pq[1], mode), pairs))


def route_through(points, mode="car"):
    """Маршрут по порядку через точки [{name, lat, lon, id?}, …]. Участки считаются параллельно.
    Возвращает {legs, distance_km, duration_min, failed}: суммы — только по построенным участкам.
    Если у точек есть id (pN), в каждом участке лежит legs[i]["ids"] = (id_откуда, id_куда): по ним инструмент
    route_by_places запоминает готовые участки в контексте запроса, и карта в конце не строит их заново."""
    pairs = [(p, q) for p, q in zip(points, points[1:])
             if haversine_m(p["lon"], p["lat"], q["lon"], q["lat"]) > SAME_PLACE_KM * 1000]
    if not pairs:
        return {"legs": [], "distance_km": 0, "duration_min": 0, "failed": 0}
    legs = route_legs(pairs, mode)
    for (p, q), leg in zip(pairs, legs):
        leg["ids"] = (p.get("id"), q.get("id"))
    ok = [l for l in legs if l["ok"]]
    return {
        "legs": legs,
        "distance_km": round(sum(l["distance_km"] for l in ok), 1),
        "duration_min": sum(l["duration_min"] for l in ok),
        "failed": len(legs) - len(ok),
    }
