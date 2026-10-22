"""Контекст одного запроса к агенту: найденные места с короткими id и разбор маршрута из ответа модели.

Идея: инструменты НЕ рисуют на карте всё подряд. Каждому найденному месту присваивается id (p1, p2, …),
а сервер подставляет координаты по строке «ROUTE: p3 p1 p7 | p5 p2» или по id из вызовов route_by_places.
Так на карте остаются только точки итогового плана, а координаты не выдумываются.
Файл начинается с «_», поэтому реестр инструментов его не загружает.
"""
import re
import threading

DEFAULT_BEACH_LIMIT = 3   # пляжей в ответе, если пользователь не назвал число
MAX_BEACH_LIMIT = 10      # потолок, даже если просят больше

MAX_ROUTE_POINTS = 25
_ROUTE_LINE = re.compile(r"(?im)^[ \t>*`_#-]*ROUTE[ \t]*:[ \t]*(.*?)[ \t`*_]*$")
_ID = re.compile(r"\bp(\d{1,4})\b", re.I)


def new_context(beach_limit=DEFAULT_BEACH_LIMIT):
    # legs — готовые участки дорог {(id_откуда, id_куда, режим): участок}: их строит route_by_places, пока модель составляет план
    return {"places": {}, "legs": {}, "_n": 0, "_lock": threading.RLock(), "beach_limit": int(beach_limit)}


def store_leg(ctx, a_id, b_id, mode, leg):
    """Запоминает построенный участок дороги между местами a_id и b_id (только удачные)."""
    if a_id and b_id and leg.get("ok"):
        with ctx.setdefault("_lock", threading.RLock()):
            ctx.setdefault("legs", {})[(a_id, b_id, mode)] = leg


def register_place(ctx, name, lat, lon, kind=None):
    """Запоминает место и возвращает его id. Одно и то же место (имя + координаты) получает один id.
    kind="beach" помечает пляж — по этой метке считается лимит пляжей."""
    lock = ctx.setdefault("_lock", threading.RLock())
    key = (str(name).strip().lower(), round(float(lat), 3), round(float(lon), 3))
    with lock:
        places = ctx.setdefault("places", {})
        for pid, p in places.items():
            if p["_key"] == key:
                if kind and not p.get("kind"):
                    p["kind"] = kind
                return pid
        ctx["_n"] = ctx.get("_n", 0) + 1
        pid = f"p{ctx['_n']}"
        places[pid] = {"name": str(name)[:120], "lat": float(lat), "lon": float(lon), "_key": key, "kind": kind}
        return pid


# ---------- пляжи: лимит на весь ответ, а не на один вызов инструмента ----------

_BEACH_NAME = re.compile(r"пляж|beach", re.I)
_NOT_BEACH = re.compile(r"кафе|ресторан|\bбар\b|отел|гостиниц|баз[аыу] |санатор|магазин|кемпинг|парковк", re.I)
_NUMBERS = {"один": 1, "одну": 1, "одна": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "пару": 2, "пара": 2,
            "парочку": 2, "три": 3, "трех": 3, "трёх": 3, "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5,
            "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9,
            "девяти": 9, "десять": 10, "десяти": 10}
_NOT_COUNT = re.compile(r"^(дн|ноч|час|мин|недел|чел|км|руб|раз|выходн|сут|тыс)", re.I)  # «3 дня на пляже» — не 3 пляжа
_BEACH_REQUEST = re.compile(
    r"(?<!\w)(\d{1,3}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")\s+(?:([а-яёa-z-]+)\s+)?пляж\w*", re.I)


def is_beach_name(name):
    name = str(name or "")
    return bool(_BEACH_NAME.search(name)) and not _NOT_BEACH.search(name)   # «Кафе у пляжа» — не пляж


def parse_beach_request(text):
    """Сколько пляжей просит пользователь: «три пляжа», «3 лучших пляжа», «пару пляжей».
    Возвращает (число, назвал_ли_пользователь_число). Без числа — DEFAULT_BEACH_LIMIT. Не больше MAX_BEACH_LIMIT."""
    for m in _BEACH_REQUEST.finditer(str(text or "")):
        middle = m.group(2)
        if middle and _NOT_COUNT.match(middle):
            continue
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _NUMBERS[raw]
        if n >= 1:
            return min(n, MAX_BEACH_LIMIT), True
    return DEFAULT_BEACH_LIMIT, False


def claim_beaches(ctx, candidates):
    """Пропускает через общий лимит пляжей запроса. candidates — [{name, lat, lon, ...}] по убыванию приоритета.
    Возвращает (принятые, отброшено_сколько). Пляж, который уже брали раньше, не занимает новое место.
    Потокобезопасно: инструменты работают параллельно."""
    lock = ctx.setdefault("_lock", threading.RLock())
    limit = int(ctx.get("beach_limit", DEFAULT_BEACH_LIMIT))
    accepted, dropped = [], 0
    with lock:
        places = ctx.setdefault("places", {})
        taken = sum(1 for p in places.values() if p.get("kind") == "beach")
        for c in candidates:
            key = (str(c["name"]).strip().lower(), round(float(c["lat"]), 3), round(float(c["lon"]), 3))
            known = any(p["_key"] == key and p.get("kind") == "beach" for p in places.values())
            if not known and taken >= limit:
                dropped += 1
                continue
            if not known:
                taken += 1
            accepted.append(c)
    return accepted, dropped


def chosen_beaches(ctx):
    return [p["name"] for p in (ctx.get("places") or {}).values() if p.get("kind") == "beach"]


def split_route(answer):
    """Вырезает из ответа строку «ROUTE: …». Возвращает (текст_без_строки, [[id, …] по дням])."""
    text = answer or ""
    matches = list(_ROUTE_LINE.finditer(text))
    if not matches:
        return text.strip(), []
    last = matches[-1]
    days = [[f"p{n}" for n in _ID.findall(chunk)] for chunk in last.group(1).split("|")]
    cleaned = _ROUTE_LINE.sub("", text).rstrip()
    return cleaned.strip(), [d for d in days if d]


def build_map_payload(ctx, route_days, mode="car"):
    """Данные для карты: точки маршрута в порядке посещения и РЕАЛЬНЫЕ линии по дорогам между соседними точками дня.
    routes — по элементу на участок между соседними точками ВСЕГО маршрута (в том числе между днями; day — день точки назначения): {day, coords [[lon, lat], …], distance_km, duration_min, from, to}.
    Участок, до которого не добраться по дорогам (или сервис не ответил), приходит как {gap: true, reason, coords: [A, B]} —
    фронтенд рисует его пунктиром и подписывает, а не выдаёт за дорогу. None, если точек нет."""
    from ._roads import route_legs, SAME_PLACE_KM, haversine_m   # здесь, чтобы не тянуть сеть при импорте

    places = ctx.get("places") or {}
    markers, marker_ids, seen_total, beaches = [], [], 0, set()
    beach_limit = int(ctx.get("beach_limit", DEFAULT_BEACH_LIMIT))
    for day_no, ids in enumerate(route_days or [], start=1):
        prev = None
        for pid in ids:
            p = places.get(pid)
            if not p or pid == prev:
                continue
            prev = pid
            if p.get("kind") == "beach":
                beaches.add(pid)
                if len(beaches) > beach_limit:
                    continue
            seen_total += 1
            if seen_total > MAX_ROUTE_POINTS:
                break
            markers.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "day": day_no})
            marker_ids.append(pid)
    if not markers:
        return None

    marked = list(zip(markers, marker_ids))
    pairs = [(a, b, ia, ib) for (a, ia), (b, ib) in zip(marked, marked[1:])
             if haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]) > SAME_PLACE_KM * 1000]   # и между днями: путь сплошной
    routes = []
    if pairs:
        # Участки, которые route_by_places уже построил во время планирования, берём готовыми; считаем только недостающие
        # (например, переезд между днями или участок, который не вышел с первого раза).
        with ctx.setdefault("_lock", threading.RLock()):
            stored = dict(ctx.get("legs") or {})
        legs = [stored.get((ia, ib, mode)) for _, _, ia, ib in pairs]
        missing = [i for i, leg in enumerate(legs) if leg is None]
        for i, leg in zip(missing, route_legs([(pairs[i][0], pairs[i][1]) for i in missing], mode)):
            legs[i] = leg
            store_leg(ctx, pairs[i][2], pairs[i][3], mode, leg)
        for (a, b, _, _), leg in zip(pairs, legs):
            if leg["ok"]:
                routes.append({"day": b["day"], "from": a["name"], "to": b["name"], "coords": leg["coords"],
                               "distance_km": leg["distance_km"], "duration_min": leg["duration_min"],
                               **({"note": leg["note"]} if leg.get("note") else {})})
            else:
                routes.append({"day": b["day"], "from": a["name"], "to": b["name"], "gap": True, "reason": leg["reason"],
                               "note": leg["note"], "coords": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]})
    return {"markers": markers, "routes": routes}
