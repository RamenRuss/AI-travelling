"""МОК-инструменты: АЗС, жильё, транспорт. Данные берутся из CSV в app/data/mock/.

Инструменты для агента:
    find_fuel(city, fuel_type?)                              — АЗС и виды топлива в городе
    find_lodging(city, max_price?, lodging_type?, limit?)    — жильё и цены за ночь
    find_transport(from_city, to_city?, transport_type?, after?, limit?) — рейсы и расписание

Города можно передавать по-русски или по-английски, в любом падеже («в Находке», «из Артёма»),
с опечаткой или псевдонимом («Владик»). Внутри всё приводится к английскому ключу из cities.csv.

Ответ инструмента — всегда словарь. При проблеме в нём есть ключ "error" (текст для модели), а не исключение.
Заменить мок на настоящую БД/API: сохраните имена функций и формат ответа (docs/DATA.md).
"""
import csv
import difflib
import functools
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mock"

FUELS = {"ai92": "АИ-92", "ai95": "АИ-95", "ai98": "АИ-98", "ai100": "АИ-100", "diesel": "дизель"}
LODGING_TYPES = {"hotel": "отель", "hostel": "хостел", "guest house": "гостевой дом",
                 "recreation base": "база отдыха", "apartment": "апартаменты"}
TRANSPORT_TYPES = {"bus": "автобус", "train": "поезд", "commuter train": "электричка",
                   "aeroexpress train": "аэроэкспресс", "plane": "самолёт", "taxi": "такси",
                   "ferry": "паром", "international bus": "международный автобус"}

_PREPOSITIONS = {"в", "во", "из", "до", "на", "от", "к", "ко", "около", "по", "через", "под"}


# ----------------------------------------------------------------------------- данные

@functools.lru_cache(maxsize=None)
def _read(name):
    path = DATA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Нет файла данных {path}. Запустите: python scripts/xlsx_to_csv.py <папка с xlsx>")
    with open(path, encoding="utf-8", newline="") as f:
        return tuple(csv.DictReader(f))


def rows(name):
    """Строки CSV как список словарей (кэшируется)."""
    return [dict(r) for r in _read(name)]


def reload():
    """Сбросить кэш (после перезаписи CSV)."""
    _read.cache_clear()
    _city_index.cache_clear()


# ----------------------------------------------------------------------------- нормализация

def norm(s):
    """Нижний регистр, ё->е, только буквы и цифры: «Спасск-Дальний» -> «спасскдальний»."""
    return re.sub(r"[^0-9a-zа-я]", "", str(s).lower().replace("ё", "е"))


def _strip_preposition(text):
    words = str(text).strip().lower().split()
    if len(words) > 1 and words[0] in _PREPOSITIONS:
        words = words[1:]
    return " ".join(words)


@functools.lru_cache(maxsize=1)
def _city_index():
    index = {}
    cities = rows("cities.csv")
    for c in cities:
        names = [c["city"], c["city_ru"]] + [a for a in c["aliases"].split(";") if a.strip()]
        for n in names:
            index.setdefault(norm(n), c)
        # «Артём (Аэропорт)» -> ещё и без скобок, но только если не занято городом
        base = re.sub(r"\(.*?\)", "", c["city_ru"]).strip()
        if base and c["kind"] != "city":
            index.setdefault(norm(base), c)
    return index, cities


def resolve_city(name):
    """Название (любой язык/падеж/опечатка) -> строка из cities.csv или None."""
    if name is None or not str(name).strip():
        return None
    key = norm(_strip_preposition(name))
    if not key:
        return None
    index, cities = _city_index()
    if key in index:
        return index[key]
    # падежи: общий префикс почти всего слова («находке» ~ «находка»)
    best, best_len = None, 0
    for k, c in index.items():
        if not re.match(r"[а-я]", k) or not re.match(r"[а-я]", key):
            continue
        p = len(_common_prefix(k, key))
        if p >= max(4, min(len(k), len(key)) - 2) and abs(len(k) - len(key)) <= 3 and p > best_len:
            best, best_len = c, p
    if best:
        return best
    close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.82)
    return index[close[0]] if close else None


def _common_prefix(a, b):
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[:i]


def norm_fuel(value):
    """«АИ-95», «95-й», «бензин 95», «ДТ», «солярка» -> ключ колонки (ai95, diesel) или None."""
    s = norm(value)
    if s in {"дизель", "дизельное", "дизтопливо", "дт", "diesel", "солярка", "дизельноетопливо"}:
        return "diesel"
    digits = re.sub(r"\D", "", s)
    return f"ai{digits}" if f"ai{digits}" in FUELS else None


def norm_lodging_type(value):
    s = norm(value)
    for key, needles in (("hostel", ("хостел", "hostel")), ("guest house", ("гостев", "guest", "гестхаус", "мини")),
                         ("recreation base", ("баз", "recreation", "турбаз", "санатор")),
                         ("apartment", ("апарт", "apart", "квартир")), ("hotel", ("отел", "hotel", "гостиниц"))):
        if any(n in s for n in needles):
            return key
    return None


def norm_transport_type(value):
    s = norm(value)
    for key, needles in (("international bus", ("междунар", "international")),
                         ("commuter train", ("электричк", "commuter", "пригородн")),
                         ("aeroexpress train", ("аэроэкспресс", "aeroexpress")),
                         ("plane", ("самол", "plane", "авиа", "flight", "перелет", "перелёт")),
                         ("train", ("поезд", "train", "жд")), ("bus", ("автобус", "bus")),
                         ("taxi", ("такси", "taxi", "трансфер")), ("ferry", ("паром", "ferry", "корабл"))):
        if any(n in s for n in needles):
            return key
    return None


# ----------------------------------------------------------------------------- расписание

_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def _hhmm(h, m):
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"некорректное время {h:02d}:{m:02d}")
    return f"{h:02d}:{m:02d}"


def parse_schedule(text):
    """'06:00-21:00' | '07:00, 16:00' | '08:00' | '24/7' -> словарь с kind: range | times | always.

    Бросает ValueError для любого другого формата (это ловит скрипт конвертации и тесты)."""
    s = str(text).strip()
    if s.lower() in {"24/7", "24x7", "24/24"}:
        return {"kind": "always", "text": "круглосуточно"}
    m = _RANGE.match(s)
    if m:
        a, b = _hhmm(m[1], m[2]), _hhmm(m[3], m[4])
        return {"kind": "range", "start": a, "end": b,
                "text": f"с {a} до {b} (точные рейсы в данных не указаны)"}
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if parts and all(_TIME.match(p) for p in parts):
        times = sorted(_hhmm(*_TIME.match(p).groups()) for p in parts)
        return {"kind": "times", "times": times, "text": "отправления в " + ", ".join(times)}
    raise ValueError(f"расписание «{text}» не в формате ЧЧ:ММ-ЧЧ:ММ, ЧЧ:ММ[, ЧЧ:ММ…] или 24/7")


def _parse_after(after):
    m = _TIME.match(str(after).strip())
    if not m:
        raise ValueError("after должен быть временем в формате ЧЧ:ММ, например 18:30")
    return _hhmm(*m.groups())


# ----------------------------------------------------------------------------- общие помощники

def _unknown_city(name, scope):
    cities = [c["city_ru"] for c in rows("cities.csv") if c["kind"] == "city"]
    return {"error": f"Город «{name}» не найден в демо-данных ({scope}). Доступные города: {', '.join(cities)}"}


def _city_info(c):
    d = {"city": c["city_ru"], "city_en": c["city"]}
    if c["lat"] and c["lon"]:
        d["location"] = {"lat": float(c["lat"]), "lon": float(c["lon"]), "approximate": True}
    return d


def _add_marker(ctx, c, label):
    """Раньше добавляла город на карту. Теперь на карте только точки итогового маршрута (см. tools/_ctx.py),
    поэтому демо-инструменты карту не трогают. Функция оставлена, чтобы не менять вызовы."""
    return None


def _positive_number(value, what):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} должен быть числом (рубли за ночь)") from None
    if v <= 0:
        raise ValueError(f"{what} должен быть больше нуля")
    return v


# ----------------------------------------------------------------------------- инструменты

def find_fuel(ctx, city, fuel_type=None):
    """АЗС города и какие виды топлива есть."""
    c = resolve_city(city)
    if c is None:
        return _unknown_city(city, "АЗС")
    fuel_key = None
    if fuel_type not in (None, ""):
        fuel_key = norm_fuel(fuel_type)
        if fuel_key is None:
            return {"error": f"Неизвестный вид топлива «{fuel_type}». Допустимо: {', '.join(FUELS.values())}"}
    stations = [r for r in rows("gas_stations.csv") if r["city"] == c["city"]]
    if not stations:
        with_gas = sorted({r["city"] for r in rows("gas_stations.csv")})
        ru = {x["city"]: x["city_ru"] for x in rows("cities.csv")}
        return {**_city_info(c), "stations_total": 0,
                "note": f"В демо-данных нет АЗС в городе {c['city_ru']}. АЗС есть в: {', '.join(ru[x] for x in with_gas)}"}
    have = [k for k in FUELS if any(s[k] == "1" for s in stations)]
    out = {**_city_info(c), "stations_total": len(stations),
           "available_fuels": [FUELS[k] for k in have],
           "missing_fuels": [FUELS[k] for k in FUELS if k not in have]}
    shown = [s for s in stations if fuel_key is None or s[fuel_key] == "1"]
    out["stations"] = [{"name": s["station_name"], "fuels": [FUELS[k] for k in FUELS if s[k] == "1"]} for s in shown]
    if fuel_key:
        out["fuel_requested"] = FUELS[fuel_key]
        out["available_in_city"] = bool(shown)
        if not shown:
            out["note"] = f"В городе {c['city_ru']} нет АЗС с топливом {FUELS[fuel_key]} (в демо-данных)"
    _add_marker(ctx, c, "АЗС: " + ", ".join(out["available_fuels"]))
    return out


def find_lodging(ctx, city, max_price=None, lodging_type=None, limit=10):
    """Жильё в городе: фильтр по цене за ночь и типу, сортировка от дешёвого к дорогому."""
    c = resolve_city(city)
    if c is None:
        return _unknown_city(city, "жильё")
    filters = {}
    try:
        if max_price not in (None, ""):
            filters["max_price"] = _positive_number(max_price, "max_price")
        limit = int(limit) if limit not in (None, "") else 10
        if limit < 1:
            raise ValueError("limit должен быть не меньше 1")
    except ValueError as e:
        return {"error": str(e)}
    type_key = None
    if lodging_type not in (None, ""):
        type_key = norm_lodging_type(lodging_type)
        if type_key is None:
            return {"error": f"Неизвестный тип жилья «{lodging_type}». Допустимо: {', '.join(LODGING_TYPES.values())}"}
        filters["type"] = LODGING_TYPES[type_key]
    all_in_city = [r for r in rows("accommodation.csv") if r["city"] == c["city"]]
    if not all_in_city:
        return {**_city_info(c), "found": 0, "options": [], "note": f"В демо-данных нет жилья в городе {c['city_ru']}"}
    pool = [r for r in all_in_city if type_key is None or r["type"] == type_key]
    matched = [r for r in pool if "max_price" not in filters or int(r["price_per_night_rub"]) <= filters["max_price"]]
    matched.sort(key=lambda r: (int(r["price_per_night_rub"]), r["name"]))
    out = {**_city_info(c), "found": len(matched), "filters": filters, "truncated": len(matched) > limit,
           "options": [{"name": r["name"], "type": LODGING_TYPES.get(r["type"], r["type"]),
                        "price_per_night_rub": int(r["price_per_night_rub"])} for r in matched[:limit]]}
    if not matched:
        if pool:
            cheapest = min(int(r["price_per_night_rub"]) for r in pool)
            out["note"] = f"Подходящего жилья в городе {c['city_ru']} нет; самый дешёвый вариант с такими параметрами — {cheapest} ₽ за ночь"
        else:
            types = sorted({LODGING_TYPES.get(r["type"], r["type"]) for r in all_in_city})
            out["note"] = f"В городе {c['city_ru']} нет жилья типа «{filters['type']}». Есть: {', '.join(types)}"
    _add_marker(ctx, c, f"Жильё: {len(all_in_city)} вариантов")
    return out


def find_transport(ctx, from_city, to_city=None, transport_type=None, after=None, limit=10):
    """Рейсы из города (и, если задано, в конкретный город). Цены в рублях, расписание как в данных."""
    a = resolve_city(from_city)
    if a is None:
        return _unknown_city(from_city, "транспорт")
    b = None
    if to_city not in (None, ""):
        b = resolve_city(to_city)
        if b is None:
            return {"error": f"Пункт назначения «{to_city}» не найден в демо-данных. "
                             f"Куда можно уехать из {a['city_ru']}: {', '.join(_destinations(a))}"}
    type_key = None
    if transport_type not in (None, ""):
        type_key = norm_transport_type(transport_type)
        if type_key is None:
            return {"error": f"Неизвестный вид транспорта «{transport_type}». Допустимо: {', '.join(TRANSPORT_TYPES.values())}"}
    try:
        after_t = _parse_after(after) if after not in (None, "") else None
        limit = int(limit) if limit not in (None, "") else 10
        if limit < 1:
            raise ValueError("limit должен быть не меньше 1")
    except ValueError as e:
        return {"error": str(e)}

    ru = {x["city"]: x["city_ru"] for x in rows("cities.csv")}
    found = [r for r in rows("transport.csv") if r["from"] == a["city"] and (b is None or r["to"] == b["city"])
             and (type_key is None or r["transport_type"] == type_key)]
    base = {"from": a["city_ru"], "from_en": a["city"]}
    if b:
        base.update({"to": b["city_ru"], "to_en": b["city"]})
    if not found:
        note = (f"Прямых рейсов {a['city_ru']} — {b['city_ru']} в демо-данных нет. " if b else
                f"Из города {a['city_ru']} в демо-данных нет рейсов. ")
        note += f"Направления из города {a['city_ru']}: {', '.join(_destinations(a))}" if _destinations(a) else ""
        if type_key and b and any(r["from"] == a["city"] and r["to"] == b["city"] for r in rows("transport.csv")):
            note = f"Рейсов вида «{TRANSPORT_TYPES[type_key]}» по этому маршруту нет, но другие виды транспорта есть"
        return {**base, "direct": False, "found": 0, "options": [], "note": note.strip()}

    options = []
    for r in found:
        sch = parse_schedule(r["schedule"])
        opt = {"to": ru.get(r["to"], r["to"]), "transport_type": TRANSPORT_TYPES.get(r["transport_type"], r["transport_type"]),
               "price_rub": int(r["price_rub"]), "departure_point": r["departure_point"],
               "departure_address": r["departure_address"], "schedule": sch["text"], "schedule_kind": sch["kind"]}
        if sch["kind"] == "times":
            opt["departures"] = sch["times"]
        if after_t:
            if sch["kind"] == "times":
                later = [t for t in sch["times"] if t >= after_t]
                if not later:
                    continue
                opt["next_departure"] = later[0]
            elif sch["kind"] == "range" and sch["end"] > sch["start"] and sch["end"] <= after_t:
                continue
        options.append(opt)
    options.sort(key=lambda o: (o["price_rub"], o["transport_type"]))
    out = {**base, "direct": True, "found": len(options), "truncated": len(options) > limit, "options": options[:limit]}
    if after_t:
        out["after"] = after_t
        if not options:
            out["note"] = f"После {after_t} рейсов нет — все отправления раньше. Попробуйте утро следующего дня."
    if b is None:
        out["destinations"] = _destinations(a)
    _add_marker(ctx, a, "Отправление")
    return out


def _destinations(a):
    ru = {x["city"]: x["city_ru"] for x in rows("cities.csv")}
    return sorted({ru.get(r["to"], r["to"]) for r in rows("transport.csv") if r["from"] == a["city"]})


# ----------------------------------------------------------------------------- регистрация в реестре

TOOLS = [
    {"type": "function", "function": {
        "name": "find_fuel",
        "description": "АЗС в городе Приморья и какие виды топлива (АИ-92/95/98/100, дизель) там есть. "
                       "Используй, когда спрашивают про заправки или топливо на маршруте. Данные демонстрационные.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "Город, например «Находка»"},
            "fuel_type": {"type": "string", "description": "Необязательно: АИ-92, АИ-95, АИ-98, АИ-100 или дизель"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "find_lodging",
        "description": "Жильё в городе Приморья (отели, хостелы, гостевые дома, базы отдыха, апартаменты) с ценой за ночь в рублях. "
                       "Демонстрационные данные: без дат, адресов и наличия мест.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "Город, например «Владивосток»"},
            "max_price": {"type": "number", "description": "Необязательно: максимальная цена за ночь, ₽"},
            "lodging_type": {"type": "string", "description": "Необязательно: отель, хостел, гостевой дом, база отдыха, апартаменты"},
            "limit": {"type": "integer", "description": "Сколько вариантов вернуть (по умолчанию 10)"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "find_transport",
        "description": "Автобусы, поезда, электрички, самолёты, паромы, такси из города Приморья: цена в рублях, пункт и адрес отправления, расписание. "
                       "Если to_city не указан — вернёт список направлений. Демонстрационные данные: без дней недели и длительности пути.",
        "parameters": {"type": "object", "properties": {
            "from_city": {"type": "string", "description": "Откуда, например «Владивосток»"},
            "to_city": {"type": "string", "description": "Необязательно: куда, например «Уссурийск» или «Москва»"},
            "transport_type": {"type": "string", "description": "Необязательно: автобус, поезд, электричка, аэроэкспресс, самолёт, такси, паром, международный автобус"},
            "after": {"type": "string", "description": "Необязательно: время ЧЧ:ММ — показать только рейсы после него"},
            "limit": {"type": "integer", "description": "Сколько вариантов вернуть (по умолчанию 10)"}},
            "required": ["from_city"]}}},
]

TOOL_FUNCS = {"find_fuel": find_fuel, "find_lodging": find_lodging, "find_transport": find_transport}

STEP_LABELS = {"find_fuel": "Проверяю АЗС по маршруту", "find_lodging": "Подбираю жильё",
               "find_transport": "Смотрю транспорт и расписание"}
