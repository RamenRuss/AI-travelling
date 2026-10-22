"""⚠️ МОК. Тулы на CSV: АЗС, жильё, расписания. Данные вымышленные: app/data/mock/*.csv.

Сейчас агентом НЕ используются (в app/tools/real.py их нет) — подключить, когда понадобятся:
добавить схемы в TOOLS и функции в TOOL_FUNCS. Функции принимают (city, ...) без ctx.
"""
import csv
import os
from datetime import date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "mock")


def norm(s):
    """Нормализация названий: регистр, пробелы, ё -> е, префикс «г.»."""
    s = str(s or "").strip().casefold().replace("ё", "е")
    for prefix in ("г. ", "г.", "город ", "пгт ", "пос. "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s


def _load(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","  # Excel (рус.) любит ;
    rows = csv.DictReader(text.splitlines(), delimiter=delim)
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def _missing(name):
    return {"error": f"Файл {name} не найден в tools/data/"}


def _yes(v):
    return norm(v) in ("да", "yes", "true", "1")


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------- АЗС ----------

def find_fuel(city):
    """АЗС в городе и наличие бензина АИ-95 и дизеля."""
    rows = _load("fuel.csv")
    if rows is None:
        return _missing("fuel.csv")
    c = norm(city)
    stations = [
        {"name": r.get("name"), "city": r.get("city"), "lat": _num(r.get("lat")),
         "lon": _num(r.get("lon")), "ai95": _yes(r.get("ai95")), "diesel": _yes(r.get("diesel"))}
        for r in rows if norm(r.get("city")) == c
    ]
    res = {
        "city": city,
        "stations": stations,
        "has_ai95": any(s["ai95"] for s in stations),
        "has_diesel": any(s["diesel"] for s in stations),
        "source": "mock fuel.csv",
    }
    if not stations:
        res["note"] = (f"В данных нет АЗС в городе {city}. "
                       f"Города в данных: {', '.join(sorted({r.get('city') for r in rows}))}")
    return res


# ---------- Жильё ----------

def find_lodging(city, max_price=None):
    """Жильё в городе, от дешёвого к дорогому. max_price — максимум за ночь, руб."""
    rows = _load("lodging.csv")
    if rows is None:
        return _missing("lodging.csv")
    c = norm(city)
    limit = _num(max_price) if max_price is not None else None
    items = []
    for r in rows:
        if norm(r.get("city")) != c:
            continue
        price = _num(r.get("price_per_night"))
        if limit is not None and price is not None and price > limit:
            continue
        items.append({"name": r.get("name"), "city": r.get("city"), "type": r.get("type"),
                      "price_per_night": price, "lat": _num(r.get("lat")), "lon": _num(r.get("lon"))})
    items.sort(key=lambda x: x["price_per_night"] if x["price_per_night"] is not None else 1e9)
    res = {"city": city, "max_price": limit, "options": items, "source": "mock lodging.csv"}
    if not items:
        res["note"] = "Подходящего жилья в данных нет (проверь город и бюджет)"
    return res


# ---------- Расписание ----------

_DAY_NAMES = [
    ("пн", "понедельник", "monday"), ("вт", "вторник", "tuesday"),
    ("ср", "среда", "wednesday"), ("чт", "четверг", "thursday"),
    ("пт", "пятница", "friday"), ("сб", "суббота", "saturday"),
    ("вс", "воскресенье", "sunday"),
]
_DAY_IDX = {}
for _i, (_s, _r, _e) in enumerate(_DAY_NAMES):
    for _k in (_s, _r, _e, _e[:3]):
        _DAY_IDX[_k] = _i


def parse_weekday(w):
    """'пн', 'пятница', 'friday', '2026-10-24' или число 0-6 -> индекс 0..6 (пн=0). None — не задан."""
    if w is None or w == "":
        return None
    if isinstance(w, int):
        return w % 7
    s = str(w).strip().casefold()
    try:
        return date.fromisoformat(s).weekday()
    except ValueError:
        pass
    return _DAY_IDX.get(s)


def _days_match(spec, idx):
    spec = norm(spec)
    if spec in ("ежедневно", "", "все", "каждый день"):
        return True
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = [_DAY_IDX.get(x.strip()) for x in part.split("-", 1)]
            if a is None or b is None:
                continue
            ok = a <= idx <= b if a <= b else (idx >= a or idx <= b)
            if ok:
                return True
        elif _DAY_IDX.get(part) == idx:
            return True
    return False


def get_schedule(from_city, to_city, weekday=None):
    """Рейсы автобусов и электричек. weekday: 'пн'..'вс' или дата ГГГГ-ММ-ДД."""
    rows = _load("schedule.csv")
    if rows is None:
        return _missing("schedule.csv")

    idx = parse_weekday(weekday)
    if weekday not in (None, "") and idx is None:
        return {"error": f"Не понял день недели: {weekday}. Используй пн..вс или дату ГГГГ-ММ-ДД"}

    f, t = norm(from_city), norm(to_city)
    direction = [r for r in rows if norm(r.get("from")) == f and norm(r.get("to")) == t]
    trips = [
        {"type": r.get("type"), "depart": r.get("depart"),
         "duration_min": _num(r.get("duration_min")), "price": _num(r.get("price")),
         "days": r.get("days")}
        for r in direction if idx is None or _days_match(r.get("days", ""), idx)
    ]
    trips.sort(key=lambda x: x["depart"] or "")

    res = {"from": from_city, "to": to_city,
           "weekday": _DAY_NAMES[idx][0] if idx is not None else None,
           "trips": trips, "source": "mock schedule.csv"}
    if not direction:
        res["note"] = "Прямых рейсов по этому направлению в данных нет"
    elif not trips:
        res["note"] = ("В этот день рейсов нет. Рейсы ходят: "
                       + ", ".join(sorted({r.get('days', '') for r in direction})))
    return res
