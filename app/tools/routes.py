"""Инструмент route_by_places: РЕАЛЬНЫЙ маршрут по дорогам между найденными местами (по их id).

Модель передаёт id мест в порядке посещения, а координаты подставляет сервер, поэтому их нельзя выдумать.
Ответ — расстояние и время по каждому перегону и итого. Дороги строятся здесь, пока идёт составление плана, и
запоминаются в контексте запроса (ctx["legs"]); в конце build_map_payload берёт готовые участки для карты.
"""
from ._ctx import MAX_ROUTE_POINTS, store_leg
from ._roads import route_through

def route_by_places(ctx, stops, mode="car"):
    if isinstance(stops, str):
        stops = stops.replace(",", " ").split()
    if not isinstance(stops, list) or len(stops) < 2:
        return {"error": "Нужно минимум два id мест из результатов поиска, например [\"p1\", \"p3\"]"}
    places = ctx.get("places") or {}
    unknown = [s for s in stops if s not in places]
    if unknown:
        return {"error": f"Нет мест с id {unknown}. Используй только id из ответов инструментов (поле id)."}
    if len(stops) > MAX_ROUTE_POINTS:
        return {"error": f"Не больше {MAX_ROUTE_POINTS} точек за один вызов"}
    mode = mode if mode in ("car", "walk", "bike") else "car"

    r = route_through([{**places[s], "id": s} for s in stops], mode)
    for l in r["legs"]:                       # готовые участки запоминаем: карта в конце ответа берёт их без повторного расчёта
        a_id, b_id = l.get("ids") or (None, None)
        store_leg(ctx, a_id, b_id, mode, l)
    legs = [{"from": l["from"], "to": l["to"], "ok": l["ok"],
             **({"distance_km": l["distance_km"], "duration_min": l["duration_min"]} if l["ok"] else {}),
             **({"note": l["note"]} if l.get("note") else {})} for l in r["legs"]]
    out = {"stops": stops, "mode": mode, "legs": legs, "total_km": r["distance_km"], "total_min": r["duration_min"]}
    if r["failed"]:
        out["warning"] = (f"Участков без построенного маршрута: {r['failed']}. Не называй для них расстояние и время; "
                          "если дороги нет — скажи про паром/катер или предложи другое место.")
    return out


TOOL_FUNCS = {"route_by_places": route_by_places}

TOOLS = [{
    "type": "function",
    "function": {
        "name": "route_by_places",
        "description": ("Реальный маршрут по дорогам между местами плана: расстояние и время по каждому перегону и итого. "
                        "Передай id мест (pN из результатов поиска) в порядке посещения через ВСЕ дни сразу — один вызов на весь маршрут, от старта до финиша. "
                        "Только по этим данным называй километры и время в пути."),
        "parameters": {
            "type": "object",
            "properties": {
                "stops": {"type": "array", "items": {"type": "string"}, "description": "id мест по порядку, от 2 штук, например [\"p1\",\"p4\",\"p2\"]"},
                "mode": {"type": "string", "enum": ["car", "walk", "bike"]},
            },
            "required": ["stops"],
        },
    },
}]

STEP_LABELS = {"route_by_places": "Строю маршрут по дорогам"}
