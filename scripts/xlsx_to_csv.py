"""Excel -> CSV для мок-данных (АЗС, жильё, транспорт) с проверкой.

Положите xlsx в любую папку (имена файлов не важны — тип определяется по заголовкам колонок):

    python scripts/xlsx_to_csv.py путь/к/папке
    python scripts/xlsx_to_csv.py gas.xlsx hotels.xlsx transport.xlsx

Результат: app/data/mock/gas_stations.csv, accommodation.csv, transport.csv.

Что делает скрипт:
  * убирает полные дубли строк;
  * приводит названия городов к английскому ключу из app/data/mock/cities.csv («Находка» -> «Nakhodka»);
  * проверяет значения (флаги топлива 0/1, цены > 0, формат расписания, известные города);
  * НЕ записывает файл, если нашёл ошибки, и называет номер строки в Excel.

Нужен openpyxl: pip install -r requirements-dev.txt
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402
from app.tools import mock_data as md  # noqa: E402

OUT_DIR = md.DATA_DIR
SPECS = {
    "gas_stations.csv": ["station_name", "city", "ai92", "ai95", "ai98", "ai100", "diesel"],
    "accommodation.csv": ["name", "city", "type", "price_per_night_rub"],
    "transport.csv": ["from", "to", "departure_point", "departure_address", "price_rub", "transport_type", "schedule"],
}
CITY_COLS = {"gas_stations.csv": ["city"], "accommodation.csv": ["city"], "transport.csv": ["from", "to"]}


def _clean(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v.strip() if isinstance(v, str) else v


def _positive_int(value, errors, where, what):
    try:
        if int(value) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"{where}: {what} должно быть положительным числом, а там «{value}»")


def check(out_name, recs):
    """Возвращает (errors, warnings) для списка словарей."""
    errors, warnings = [], []
    for i, r in enumerate(recs, start=2):  # 2 — первая строка данных в Excel (номера могут сместиться после удаления дублей)
        where = f"запись {i}"
        for col in CITY_COLS[out_name]:
            if md.resolve_city(r[col]) is None:
                errors.append(f"{where}: города «{r[col]}» нет в app/data/mock/cities.csv — добавьте его туда "
                              f"(английское название, русское, тип city/destination, координаты)")
        if out_name == "gas_stations.csv":
            for k in ("ai92", "ai95", "ai98", "ai100", "diesel"):
                if r[k] not in (0, 1):
                    errors.append(f"{where}: {k} должно быть 0 или 1, а там «{r[k]}»")
        elif out_name == "accommodation.csv":
            _positive_int(r["price_per_night_rub"], errors, where, "price_per_night_rub")
            if r["type"] not in md.LODGING_TYPES:
                warnings.append(f"{where}: неизвестный тип жилья «{r['type']}» (известны: {', '.join(md.LODGING_TYPES)})")
        else:
            _positive_int(r["price_rub"], errors, where, "price_rub")
            if r["transport_type"] not in md.TRANSPORT_TYPES:
                warnings.append(f"{where}: неизвестный вид транспорта «{r['transport_type']}» (известны: {', '.join(md.TRANSPORT_TYPES)})")
            try:
                md.parse_schedule(r["schedule"])
            except ValueError as e:
                errors.append(f"{where}: {e}")
    return errors, warnings


def convert(path):
    """Один xlsx -> (имя csv, заголовок, записи, дубли, ошибки, предупреждения, переименовано городов).
    None, если заголовки не совпали ни с одним форматом."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    it = wb.worksheets[0].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(it, [])]
    out_name = next((n for n, cols in SPECS.items() if header == cols), None)
    if out_name is None:
        return None
    recs, seen, dups, renamed = [], set(), [], 0
    for n, row in enumerate(it, start=2):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        rec = {h: _clean(v) for h, v in zip(header, row)}
        for col in CITY_COLS[out_name]:  # «Находка»/«nakhodka» -> «Nakhodka»: по этому ключу ищут инструменты
            c = md.resolve_city(rec[col])
            if c and c["city"] != rec[col]:
                rec[col] = c["city"]
                renamed += 1
        key = tuple(rec.values())
        if key in seen:
            dups.append((n, rec))
            continue
        seen.add(key)
        recs.append(rec)
    errors, warnings = check(out_name, recs)
    return out_name, header, recs, dups, errors, warnings, renamed


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    files = []
    for a in argv:
        p = Path(a)
        files += sorted(p.glob("*.xlsx")) if p.is_dir() else [p]
    if not files:
        print("xlsx-файлов не найдено")
        return 2
    failed, done = False, set()
    for f in files:
        print(f"\n== {f.name}")
        res = convert(f)
        if res is None:
            print("  пропущен: заголовки колонок не совпали ни с одним из трёх форматов (см. docs/DATA.md)")
            continue
        out_name, header, recs, dups, errors, warnings, renamed = res
        print(f"  это «{out_name}»: {len(recs)} строк")
        for n, rec in dups:
            print(f"  убран дубль (строка {n}): {', '.join(str(v) for v in rec.values())}")
        if renamed:
            print(f"  названий городов приведено к единому виду: {renamed}")
        for w in warnings:
            print(f"  ⚠ {w}")
        if errors:
            failed = True
            for e in errors:
                print(f"  ✖ {e}")
            print("  файл НЕ записан — исправьте ошибки в Excel и запустите снова")
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / out_name, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows([[r[h] for h in header] for r in recs])
        done.add(out_name)
        print(f"  записано: app/data/mock/{out_name}")
    missing = set(SPECS) - done
    if missing:
        print(f"\nНе обновлены: {', '.join(sorted(missing))} (остались прежние версии, если были)")
    md.reload()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
