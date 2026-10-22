"""Проверка сервисов маршрутов с ВАШЕГО компьютера: что доступно и строит ли дорогу.
Запуск из папки проекта: python scripts/check_routing.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402
from app.tools import _roads  # noqa: E402

A = (131.8869, 43.1155)    # Владивосток
B = (132.8935, 42.8154)    # Находка
print("Владивосток -> Находка, авто\n")

checks = [("OSRM routing.openstreetmap.de (авто)", lambda: _roads._fossgis(A, B, "car")),
          ("OSRM routing.openstreetmap.de (пешком)", lambda: _roads._fossgis(A, B, "walk")),
          ("OSRM router.project-osrm.org (авто)", lambda: _roads._osrm(A, B))]
if config.ORS_KEY:
    checks.insert(0, ("OpenRouteService (ключ задан)", lambda: _roads._ors(A, B, "car")))
else:
    print("ORS_API_KEY не задан — OpenRouteService пропущен (это нормально)\n")

ok = 0
for name, fn in checks:
    try:
        dist, dur, coords, _ = fn()
        print(f"OK    {name}: {dist / 1000:.0f} км, {dur / 60:.0f} мин, точек в линии: {len(coords)}")
        ok += 1
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  {name}: {type(e).__name__}: {e}")

print("\nИтог:", "дорога строится" if ok else "НИ ОДИН сервис не ответил — проверьте интернет/VPN/файрвол: без них линии по дорогам не построить")
