"""Тесты данных и инструментов (АЗС, жильё, транспорт). Сеть и ключи не нужны: python -m pytest -q"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import contract  # noqa: E402
from app.tools import mock_data as md  # noqa: E402
from app.tools import registry  # noqa: E402


class DataIntegrity(unittest.TestCase):
    def test_all_cities_known(self):
        for f, cols in (("gas_stations.csv", ["city"]), ("accommodation.csv", ["city"]), ("transport.csv", ["from", "to"])):
            for r in md.rows(f):
                for c in cols:
                    self.assertIsNotNone(md.resolve_city(r[c]), f"{f}: {r[c]}")
                    self.assertEqual(md.resolve_city(r[c])["city"], r[c], "в CSV должен быть канонический ключ")

    def test_schedules_and_values(self):
        for r in md.rows("transport.csv"):
            md.parse_schedule(r["schedule"])
            self.assertGreater(int(r["price_rub"]), 0)
            self.assertIn(r["transport_type"], md.TRANSPORT_TYPES)
        for r in md.rows("accommodation.csv"):
            self.assertIn(r["type"], md.LODGING_TYPES)
        for r in md.rows("gas_stations.csv"):
            for k in md.FUELS:
                self.assertIn(r[k], ("0", "1"))

    def test_no_duplicates(self):
        t = [tuple(r.values()) for r in md.rows("transport.csv")]
        self.assertEqual(len(t), len(set(t)))


class CityResolve(unittest.TestCase):
    def test_forms(self):
        for q, en in (("Находка", "Nakhodka"), ("в Находке", "Nakhodka"), ("из Артёма", "Artem"), ("Артем", "Artem"),
                      ("Владик", "Vladivostok"), ("vladivostok", "Vladivostok"), ("Владивосто", "Vladivostok"),
                      ("Спасск-Дальний", "Spassk-Dalniy"), ("Москву", "Moscow"), ("аэропорт", "Artem (Airport VVO)")):
            self.assertEqual(md.resolve_city(q)["city"], en, q)

    def test_unknown(self):
        for q in ("Лазо", "", None, "   ", "Нью-Йорк"):
            self.assertIsNone(md.resolve_city(q), q)


class Fuel(unittest.TestCase):
    def test_basic(self):
        r = md.find_fuel({}, "Находка")
        self.assertEqual(r["stations_total"], 4)
        self.assertIn("АИ-95", r["available_fuels"])

    def test_missing_fuel_in_city(self):
        r = md.find_fuel({}, "Chuguevka", "95")
        self.assertFalse(r["available_in_city"])
        self.assertEqual(r["stations"], [])
        self.assertIn("note", r)

    def test_errors(self):
        self.assertIn("error", md.find_fuel({}, "Лазо"))
        self.assertIn("error", md.find_fuel({}, "Находка", "газ"))
        self.assertEqual(md.find_fuel({}, "Москва")["stations_total"], 0)

    def test_fuel_synonyms(self):
        for q, k in (("АИ-98", "ai98"), ("98", "ai98"), ("бензин 95", "ai95"), ("ДТ", "diesel"), ("солярка", "diesel")):
            self.assertEqual(md.norm_fuel(q), k, q)


class Lodging(unittest.TestCase):
    def test_filter_sort(self):
        r = md.find_lodging({}, "Владивосток", 1500, "хостел")
        prices = [o["price_per_night_rub"] for o in r["options"]]
        self.assertEqual(prices, sorted(prices))
        self.assertTrue(all(p <= 1500 for p in prices) and r["found"] == 4)

    def test_limit_and_empty(self):
        self.assertTrue(md.find_lodging({}, "Vladivostok", limit=2)["truncated"])
        r = md.find_lodging({}, "Находка", 100)
        self.assertEqual(r["options"], [])
        self.assertIn("900", r["note"])

    def test_errors(self):
        for kw in ({"lodging_type": "шалаш"}, {"max_price": "дёшево"}, {"max_price": -5}, {"limit": 0}):
            self.assertIn("error", md.find_lodging({}, "Находка", **kw), kw)


class Transport(unittest.TestCase):
    def test_route(self):
        r = md.find_transport({}, "Владивосток", "Уссурийск")
        self.assertEqual(r["found"], 4)
        self.assertEqual([o["price_rub"] for o in r["options"]], sorted(o["price_rub"] for o in r["options"]))

    def test_after(self):
        r = md.find_transport({}, "Владивосток", "Уссурийск", after="19:00")
        train = [o for o in r["options"] if o["transport_type"] == "поезд"][0]
        self.assertEqual(train["next_departure"], "22:10")
        self.assertTrue(all(o["transport_type"] != "автобус" or o["schedule_kind"] == "range" for o in r["options"]))

    def test_after_late(self):
        r = md.find_transport({}, "Владивосток", "Москва", after="23:55")
        self.assertEqual(r["found"], 0)
        self.assertIn("note", r)

    def test_no_direct(self):
        r = md.find_transport({}, "Дальнегорск", "Славянка")
        self.assertFalse(r["direct"])
        self.assertIn("Направления из города", r["note"])

    def test_types_and_errors(self):
        self.assertEqual(md.find_transport({}, "Владивосток", "Москва", transport_type="самолёт")["found"], 1)
        self.assertEqual(md.norm_transport_type("международный автобус"), "international bus")
        self.assertEqual(md.norm_transport_type("электричка"), "commuter train")
        for kw in ({"transport_type": "ракета"}, {"after": "25:99"}, {"to_city": "Лазо"}):
            self.assertIn("error", md.find_transport({}, "Владивосток", **kw), kw)
        self.assertIn("destinations", md.find_transport({}, "Владивосток"))

    def test_schedule_parser(self):
        self.assertEqual(md.parse_schedule("24/7")["kind"], "always")
        self.assertEqual(md.parse_schedule("07:00, 16:00")["times"], ["07:00", "16:00"])
        for bad in ("утром", "25:00-26:00", ""):
            with self.assertRaises(ValueError):
                md.parse_schedule(bad)


class Registry(unittest.TestCase):
    def test_tools_present(self):
        self.assertTrue({"find_fuel", "find_lodging", "find_transport", "search_places", "get_weather", "get_route", "route_by_places"} <= set(registry.TOOL_FUNCS))

    def test_run_tool_never_raises(self):
        ctx = registry.new_context()
        for name, args in (("find_fuel", {"city": "Находка", "junk": 1}), ("nope", "{}"), ("find_fuel", "не json"),
                           ("find_fuel", "[1]"), ("find_transport", {})):
            self.assertIn("error", json.loads(registry.run_tool(name, args, ctx)))

    def test_demo_tools_do_not_touch_map(self):
        ctx = registry.new_context()
        registry.run_tool("find_fuel", {"city": "Находка"}, ctx)
        registry.run_tool("find_lodging", {"city": "Находка"}, ctx)
        self.assertIsNone(registry.build_map_payload(ctx, []))     # на карте ничего, кроме точек маршрута
        self.assertIsNone(registry.build_map_payload(registry.new_context(), []))

    def test_map_has_only_route_points(self):
        from app.tools import _ctx
        ctx = registry.new_context()
        ids = [_ctx.register_place(ctx, f"Пляж {i}", 43 + i / 10, 131.9 + i / 10) for i in (1, 2, 3)]
        self.assertEqual(ids, ["p1", "p2", "p3"])
        self.assertEqual(_ctx.register_place(ctx, "пляж 1", 43.1, 132.0), "p1")      # то же место -> тот же id
        from app.tools import _roads
        ok = lambda a, b, mode="car": {"coords": [list(a), list(b)], "distance_m": 1000, "duration_s": 60, "source": "t", "snapped_m": 0}
        with patch.object(_roads, "road_route", side_effect=ok):
            payload = registry.build_map_payload(ctx, [["p1", "p3"], ["p3", "p9"]])      # p2 — альтернатива, p9 — выдумка
        self.assertEqual([(m["name"], m["day"]) for m in payload["markers"]], [("Пляж 1", 1), ("Пляж 3", 1), ("Пляж 3", 2)])
        self.assertEqual([(r["from"], r["to"], r["day"]) for r in payload["routes"]], [("Пляж 1", "Пляж 3", 1)])   # линия только внутри дня

    def test_split_route_line(self):
        text, days = registry.split_route("# План\nТекст\n\nROUTE: p3 p1 | p2\n")
        self.assertEqual((text, days), ("# План\nТекст", [["p3", "p1"], ["p2"]]))
        self.assertEqual(registry.split_route("**ROUTE:** `p1 p2`"), ("", [["p1", "p2"]]))
        self.assertEqual(registry.split_route("Просто ответ без маршрута"), ("Просто ответ без маршрута", []))
        self.assertEqual(registry.split_route(None), ("", []))

    def test_run_chat_runs_tools_in_parallel_and_maps_only_route(self):
        from app.agent import llm
        from app.tools import real, _roads
        found = {"Маяк": [{"name": "Маяк Токаревский", "lat": 43.1, "lon": 131.9}],
                 "Пляж": [{"name": "Пляж Шамора", "lat": 43.2, "lon": 132.2}]}
        calls = lambda *q: [{"id": str(i), "function": {"name": "search_places", "arguments": json.dumps({"query": x})}}
                            for i, x in enumerate(q)]
        first_response = {"choices": [{"message": {"content": None, "tool_calls": calls("Маяк", "Пляж", "Лишнее")}}], "usage": {"total_tokens": 10}}
        used_first = False

        def scripted_llm(messages, allow_tools=True):
            nonlocal used_first
            if not used_first:
                used_first = True
                return first_response
            # Tool calls intentionally run in parallel, so p1/p2 IDs must be read from
            # their actual results rather than assuming which thread registered first.
            found_ids = {}
            for message in messages:
                if message.get("role") == "tool":
                    for place in json.loads(message["content"]).get("places", []):
                        found_ids[place["name"]] = place["id"]
            route = f"{found_ids['Пляж Шамора']} {found_ids['Маяк Токаревский']}"
            return {"choices": [{"message": {"content": f"# План\nГотово\nROUTE: {route}"}}], "usage": {"total_tokens": 5}}

        def fake_leg(a, b, mode="car"):
            return {"from": a["name"], "to": b["name"], "ok": True,
                    "coords": [[a["lon"], a["lat"]], [(a["lon"] + b["lon"]) / 2, (a["lat"] + b["lat"]) / 2],
                               [b["lon"], b["lat"]]], "distance_km": 10.0, "duration_min": 30, "source": "test"}

        with patch.object(real, "_osm_places", side_effect=lambda q, n: found.get(q, [{"name": "Лишнее место", "lat": 43.3, "lon": 132.0}])), \
                patch.object(_roads, "route_leg", side_effect=fake_leg), \
                patch.object(llm, "call_llm", side_effect=scripted_llm):
            r = llm.run_chat([{"role": "user", "content": "план"}])
        self.assertEqual(r["answer"], "# План\nГотово")
        self.assertEqual([m["name"] for m in r["map"]["markers"]], ["Пляж Шамора", "Маяк Токаревский"])  # «Лишнее» на карте нет
        self.assertEqual(r["tools_used"], ["search_places"] * 3)

    def test_find_places_validation_offline(self):
        from app.tools import osm_places
        ctx = registry.new_context()
        self.assertIn("error", osm_places.find_places(ctx, ""))
        self.assertIn("error", osm_places.find_places(ctx, "Находка", "танцы"))
        self.assertIn("error", osm_places.find_places(ctx, "Находка", "beach", limit="много"))
        self.assertEqual([osm_places._category(x) for x in ("пляжи", "Музеи", "кафе")], ["beach", "museum", "cafe"])
        self.assertIn("find_places", registry.TOOL_FUNCS)

    def test_overpass_uses_working_mirror_and_remembers_dead_one(self):
        import time
        from app.tools import osm_places
        dead, alive = osm_places.OVERPASS_SERVERS[0], osm_places.OVERPASS_SERVERS[1:]
        osm_places._down_until.clear()

        def fake(method, url, **kw):
            if url == dead:
                raise RuntimeError("ReadTimeout")
            time.sleep(0.2)                     # рабочие зеркала отвечают чуть позже упавшего
            return {"elements": [{"url": url}]}

        with patch.object(osm_places, "http_json", side_effect=fake) as request:
            js = osm_places._overpass_json("[out:json];")
            self.assertIn(js["elements"][0]["url"], alive)
            self.assertEqual(request.call_args.kwargs["retries"], 0)
            self.assertGreater(osm_places._down_until[dead], time.time())     # упавший сервер на паузе
            request.reset_mock()
            osm_places._overpass_json("[out:json];")
            self.assertNotIn(dead, [c.args[1] for c in request.call_args_list])   # и больше не опрашивается
        osm_places._down_until.clear()

    def test_find_places_falls_back_to_nominatim_when_overpass_is_down(self):
        from app.tools import osm_places
        ctx = registry.new_context()
        with patch.object(osm_places, "disk_get", return_value=None), patch.object(osm_places, "disk_put"), \
                patch.object(osm_places, "_overpass_json", side_effect=RuntimeError("ReadTimeout")), \
                patch.object(osm_places, "_nominatim_fallback", return_value=[{"name": "Пляж Лазурный", "lat": 42.8, "lon": 132.9}]):
            r = osm_places.find_places(ctx, "Находка", "beach")
        self.assertEqual(r["places"][0]["id"], "p1")
        self.assertEqual(set(r["places"][0]), {"id", "name", "lat", "lon"})       # компактный ответ: меньше токенов
        self.assertNotIn("markers", ctx)

    def test_find_places_returns_at_most_three_beaches(self):
        from app.tools import osm_places
        raw = [{"name": f"Пляж {i}", "lat": 42.8 + i / 100, "lon": 132.9} for i in range(8)]
        with patch.object(osm_places, "disk_get", return_value=raw):
            r = osm_places.find_places(registry.new_context(), "Находка", "beach", limit=10)
        self.assertEqual(len(r["places"]), 3)

    def test_find_places_all_sources_down_says_do_not_retry(self):
        from app.tools import osm_places
        with patch.object(osm_places, "disk_get", return_value=None), \
                patch.object(osm_places, "_overpass_json", side_effect=RuntimeError("down")), \
                patch.object(osm_places, "_nominatim_fallback", side_effect=RuntimeError("down")):
            r = osm_places.find_places(registry.new_context(), "Находка", "beach")
        self.assertIn("error", r)
        self.assertIn("Не повторяй", r["error"])

    def test_route_rejects_bad_coordinates(self):
        from app.tools import real
        with self.assertRaises(ValueError):
            real.get_route(registry.new_context(), 999, 131.9, 43.1, 132.9)


class BeachLimit(unittest.TestCase):
    """«Три пляжа» должно значить три во всём ответе, а не три на каждый вызов инструмента."""

    @staticmethod
    def _raw(shift, n=8):
        return [{"name": f"Пляж {shift}-{i}", "lat": 42.8 + i / 100 + shift, "lon": 132.9} for i in range(n)]

    def test_parse_request(self):
        from app.tools import _ctx
        for text, expected in (("три пляжа", (3, True)), ("покажи 3 лучших пляжа", (3, True)), ("пару пляжей", (2, True)),
                               ("Хочу 5 красивых пляжей", (5, True)), ("Один пляж", (1, True)),
                               ("100 пляжей", (10, True)),                      # потолок
                               ("5 дней на пляже", (3, False)), ("три дня пляж", (3, False)),   # это не число пляжей
                               ("пляжи в Находке", (3, False)), ("", (3, False)), (None, (3, False))):
            self.assertEqual(_ctx.parse_beach_request(text), expected, text)

    def test_limit_is_shared_between_find_places_calls(self):
        from app.tools import osm_places
        ctx = registry.new_context(3)
        with patch.object(osm_places, "disk_get", side_effect=[self._raw(0), self._raw(1)]):
            a = osm_places.find_places(ctx, "Находка", "beach", limit=6)
            b = osm_places.find_places(ctx, "Партизанск", "beach", limit=6)
        self.assertEqual(len(a["places"]), 3)
        self.assertEqual(b["places"], [])
        self.assertIn("Лимит пляжей", b["note"])

    def test_limit_follows_user_request(self):
        from app.tools import osm_places
        with patch.object(osm_places, "disk_get", return_value=self._raw(0)):
            self.assertEqual(len(osm_places.find_places(registry.new_context(2), "Находка", "beach", limit=9)["places"]), 2)
            self.assertEqual(len(osm_places.find_places(registry.new_context(5), "Находка", "beach", limit=9)["places"]), 5)

    def test_search_places_counts_beaches_too(self):
        from app.tools import osm_places, real
        ctx = registry.new_context(3)
        found = [{"name": f"Пляж Дальний {i}", "lat": 43 + i / 50, "lon": 132.5, "address": None} for i in range(5)]
        found.append({"name": "Музей-заповедник", "lat": 43.5, "lon": 132.5, "address": None})
        found.append({"name": "Кафе у пляжа", "lat": 43.6, "lon": 132.5, "address": None})
        with patch.object(osm_places, "disk_get", return_value=self._raw(0)), \
                patch.object(real, "_osm_places", return_value=found), patch.object(real.config, "GOOGLE_KEY", ""):
            osm_places.find_places(ctx, "Находка", "beach", limit=6)       # заняли все 3 места
            r = real.search_places(ctx, "пляж у Находки", 8)
        names = [x["name"] for x in r["places"]]
        self.assertEqual(names, ["Музей-заповедник", "Кафе у пляжа"])         # не-пляжи проходят, лишние пляжи — нет
        self.assertIn("Других пляжей не предлагай", r["note"])

    def test_same_beach_does_not_take_second_slot(self):
        from app.tools import _ctx
        ctx = registry.new_context(1)
        one = {"name": "Пляж А", "lat": 43.0, "lon": 132.0}
        self.assertEqual(len(_ctx.claim_beaches(ctx, [one])[0]), 1)
        _ctx.register_place(ctx, "Пляж А", 43.0, 132.0, "beach")
        self.assertEqual(len(_ctx.claim_beaches(ctx, [one])[0]), 1)   # тот же пляж — разрешён повторно
        self.assertEqual(_ctx.claim_beaches(ctx, [{"name": "Пляж Б", "lat": 44.0, "lon": 132.0}]), ([], 1))

    def test_map_never_shows_more_beaches_than_limit(self):
        from app.tools import _ctx
        ctx = registry.new_context(2)
        ids = [_ctx.register_place(ctx, f"Пляж {i}", 43 + i / 10, 132.0, "beach") for i in range(4)]   # в обход инструментов
        cafe = _ctx.register_place(ctx, "Музей", 43.9, 132.0)
        m = _ctx.build_map_payload(ctx, [ids + [cafe]])
        self.assertEqual([x["name"] for x in m["markers"]], ["Пляж 0", "Пляж 1", "Музей"])

    def test_prompt_names_exact_number(self):
        from app.agent import llm
        self.assertIn("РОВНО 3", llm.system_prompt(3, True))
        self.assertIn("не больше 3 пляжей", llm.system_prompt(3, False))
        self.assertIn("не больше 5 пляжей", llm.system_prompt(5, True))   # и в строке про ROUTE

    def test_run_chat_passes_requested_number_to_context(self):
        from app.agent import llm
        seen = {}

        def fake_call(messages, allow_tools=True):
            seen["system"] = messages[0]["content"]
            return {"choices": [{"message": {"content": "ок"}}]}

        with patch.object(llm, "call_llm", side_effect=fake_call), \
                patch.object(llm.tools, "new_context", wraps=llm.tools.new_context) as nc:
            llm.run_chat([{"role": "user", "content": "хочу три пляжа у Находки"}])
        nc.assert_called_once_with(3)
        self.assertIn("РОВНО 3", seen["system"])


class Clarify(unittest.TestCase):
    """Если для маршрута не хватает сроков/транспорта/старта — агент спрашивает, а не выдумывает."""
    ASK = "[УТОЧНЕНИЕ]\nЧтобы составить маршрут, уточните:\n1. Как добираетесь? (авто, автобус, самолёт)\n2. Откуда едете?\nМожно ответить одной строкой — остальное я допущу сам."

    def test_prompt_has_rules(self):
        from app.agent import llm
        p = llm.system_prompt()
        for needle in ("УТОЧНЕНИЯ", "[УТОЧНЕНИЕ]", "ОДНИМ сообщением", "не переспрашивай"):
            self.assertIn(needle, p)
        self.assertNotIn("ВАЖНО: уточняющие вопросы", p)
        self.assertIn("ВАЖНО: уточняющие вопросы", llm.system_prompt(clarified=True))

    def test_split_marker(self):
        from app.agent import llm
        text, flag = llm.split_clarify(self.ASK)
        self.assertTrue(flag)
        self.assertTrue(text.startswith("Чтобы составить маршрут") and "УТОЧНЕНИЕ" not in text)
        self.assertEqual(llm.split_clarify("Обычный план поездки"), ("Обычный план поездки", False))
        self.assertEqual(llm.split_clarify(None), ("", False))

    def test_asks_instead_of_planning(self):
        from app.agent import llm, real_adapter
        with patch.object(llm, "call_llm", return_value={"choices": [{"message": {"content": self.ASK}}]}) as call:
            r = real_adapter.respond("Хочу посмотреть север Приморья на два дня")
        self.assertEqual(call.call_count, 1)                    # без инструментов — сразу вопросы
        self.assertEqual((r["type"], r["title"]), ("info", "Нужно уточнить"))
        self.assertNotIn("УТОЧНЕНИЕ", r["text"])
        self.assertIn("Как добираетесь", r["text"])
        self.assertTrue(r["steps"] and r["meta"]["tools"] == 0)
        self.assertFalse(r.get("map"))

    def test_second_turn_is_told_not_to_ask_again(self):
        from app.agent import llm
        seen = []

        def fake(messages, allow_tools=True):
            seen.append(messages[0]["content"])
            return {"choices": [{"message": {"content": "**Главное**\n- допущения: автомобиль"}}]}

        history = [{"role": "user", "content": "север Приморья на два дня"},
                   {"role": "assistant", "content": self.ASK.split("\n", 1)[1]},       # на клиенте метка уже снята
                   {"role": "user", "content": "на машине из Владивостока, в субботу"}]
        with patch.object(llm, "call_llm", side_effect=fake):
            r = llm.run_chat(history)
        self.assertIn("ВАЖНО: уточняющие вопросы", seen[0])
        self.assertFalse(r["clarify"])
        # а в обычном диалоге такой пометки нет
        with patch.object(llm, "call_llm", side_effect=fake):
            llm.run_chat([{"role": "user", "content": "север Приморья на два дня"}])
        self.assertNotIn("ВАЖНО: уточняющие вопросы", seen[1])


class Contract(unittest.TestCase):
    def test_finalize_makes_frontend_safe(self):
        d = contract.finalize({"type": "plan", "text": "t"})   # неполный план -> info
        self.assertEqual(d["type"], "info")
        self.assertEqual((d["bullets"], d["followup"]), ([], ""))
        self.assertTrue(d["steps"])
        self.assertEqual(set(d["meta"]), {"tools", "tokens", "seconds"})
        self.assertEqual(contract.finalize("просто строка")["text"], "просто строка")

    def test_mock_plan_passes_finalize_as_plan(self):
        from app.agent import mock_agent
        p = mock_agent.respond("выходные в Лазовском", "car")
        p["steps"] = ["x"]
        self.assertEqual(contract.finalize(p)["type"], "plan")


if __name__ == "__main__":
    unittest.main()


class RoadRoutes(unittest.TestCase):
    """Маршруты по дорогам: сеть замокана, реальные сервисы не вызываются."""

    A = {"name": "Владивосток", "lat": 43.12, "lon": 131.89}
    B = {"name": "Находка", "lat": 42.82, "lon": 132.87}

    @staticmethod
    def _ok(a, b, mode="car"):
        return {"coords": [list(a), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.01], list(b)],
                "distance_m": 150000, "duration_s": 7200, "source": "OSRM", "snapped_m": 20}

    def test_payload_has_real_road_geometry(self):
        from app.tools import _ctx, _roads
        ctx = registry.new_context()
        p1 = _ctx.register_place(ctx, self.A["name"], self.A["lat"], self.A["lon"])
        p2 = _ctx.register_place(ctx, self.B["name"], self.B["lat"], self.B["lon"])
        with patch.object(_roads, "road_route", side_effect=self._ok):
            m = registry.build_map_payload(ctx, [[p1, p2]])
        self.assertEqual(len(m["markers"]), 2)
        self.assertEqual(len(m["routes"]), 1)
        r = m["routes"][0]
        self.assertFalse(r.get("gap"))
        self.assertGreater(len(r["coords"]), 2)          # не прямая из двух точек
        self.assertEqual((r["distance_km"], r["duration_min"]), (150.0, 120))

    def test_route_is_continuous_across_days(self):
        from app.tools import _ctx, _roads
        ctx = registry.new_context()
        ids = [_ctx.register_place(ctx, f"Точка {i}", 43 + i / 10, 131.9 + i / 10) for i in range(4)]
        with patch.object(_roads, "road_route", side_effect=self._ok):
            m = registry.build_map_payload(ctx, [ids[:2], ids[2:]])
        legs = [(r["from"], r["to"]) for r in m["routes"]]
        self.assertEqual(legs, [("Точка 0", "Точка 1"), ("Точка 1", "Точка 2"), ("Точка 2", "Точка 3")])   # день 1 → день 2 соединены

    def test_no_road_is_marked_not_drawn_as_road(self):
        from app.tools import _ctx, _roads
        ctx = registry.new_context()
        p1 = _ctx.register_place(ctx, self.A["name"], self.A["lat"], self.A["lon"])
        p2 = _ctx.register_place(ctx, self.B["name"], self.B["lat"], self.B["lon"])
        with patch.object(_roads, "road_route", side_effect=_roads.NoRoadRoute("нет")):
            m = registry.build_map_payload(ctx, [[p1, p2]])
        self.assertTrue(m["routes"][0]["gap"])
        self.assertEqual(m["routes"][0]["reason"], "no_road")

    def test_tool_uses_ids_and_returns_legs(self):
        from app.tools import _ctx, _roads
        ctx = registry.new_context()
        p1 = _ctx.register_place(ctx, self.A["name"], self.A["lat"], self.A["lon"])
        p2 = _ctx.register_place(ctx, self.B["name"], self.B["lat"], self.B["lon"])
        with patch.object(_roads, "road_route", side_effect=self._ok):
            r = json.loads(registry.run_tool("route_by_places", {"stops": [p1, p2]}, ctx))
        self.assertEqual((r["total_km"], r["total_min"]), (150.0, 120))
        self.assertEqual(r["stops"], [p1, p2])
        self.assertNotIn("coords", json.dumps(r))        # геометрию модели не отдаём
        bad = json.loads(registry.run_tool("route_by_places", {"stops": ["p1", "p99"]}, ctx))
        self.assertIn("error", bad)
        self.assertIn("error", json.loads(registry.run_tool("route_by_places", {"stops": ["p1"]}, ctx)))

    def test_service_down_reports_failure_without_numbers(self):
        from app.tools import _ctx, _roads
        ctx = registry.new_context()
        p1 = _ctx.register_place(ctx, self.A["name"], self.A["lat"], self.A["lon"])
        p2 = _ctx.register_place(ctx, self.B["name"], self.B["lat"], self.B["lon"])
        with patch.object(_roads, "road_route", side_effect=_roads.RoutingUnavailable("down")):
            r = json.loads(registry.run_tool("route_by_places", {"stops": [p1, p2]}, ctx))
        self.assertFalse(r["legs"][0]["ok"])
        self.assertNotIn("distance_km", r["legs"][0])
        self.assertIn("warning", r)

    def test_osrm_route_uses_unlimited_snap_radius(self):
        from app.tools import _roads
        body = {"code": "Ok", "routes": [{"distance": 1234, "duration": 321,
                "geometry": {"coordinates": [[131.89, 43.12], [132.2, 43.0], [132.87, 42.82]]}}]}
        with patch.object(_roads, "http_json", return_value=body) as http:
            result = _roads._osrm_like("https://routing.example/route", "test")
        self.assertEqual(result[0:2], (1234, 321))
        self.assertGreater(len(result[2]), 2)
        self.assertEqual(http.call_args.kwargs["params"]["radiuses"], "unlimited;unlimited")

    def test_osrm_fallback_is_tried_after_primary_reports_no_segment(self):
        from app.tools import _roads
        _roads._cache.clear()
        result = (150000, 7200, [[131.89, 43.12], [132.2, 43.0], [132.87, 42.82]], "OSRM fallback")
        with patch.object(_roads.config, "ORS_KEY", None), \
             patch.object(_roads, "_fossgis", side_effect=_roads.NoRoadRoute("NoSegment")), \
             patch.object(_roads, "_osrm", return_value=result) as fallback:
            route = _roads.road_route((131.89123, 43.12123), (132.87123, 42.82123), "car")
        self.assertEqual(route["source"], "OSRM fallback")
        fallback.assert_called_once()

    def test_route_tool_stops_build_multiday_map_when_model_omits_route_line(self):
        from app.agent import llm
        from app.tools import _roads, real
        found = [{"name": "Владивосток", "lat": 43.12, "lon": 131.89},
                 {"name": "Артём", "lat": 43.35, "lon": 132.18},
                 {"name": "Уссурийск", "lat": 43.8, "lon": 131.95},
                 {"name": "Спасск-Дальний", "lat": 44.6, "lon": 132.82}]
        search_call = {"id": "1", "function": {
            "name": "search_places", "arguments": '{"query":"Приморье"}'}}
        route_calls = [
            {"id": "2", "function": {
                "name": "route_by_places", "arguments": '{"stops":["p1","p2"]}'}},
            {"id": "3", "function": {
                "name": "route_by_places", "arguments": '{"stops":["p3","p4"]}'}},
        ]
        responses = iter([
            {"choices": [{"message": {"tool_calls": [search_call]}}]},
            {"choices": [{"message": {"tool_calls": route_calls}}]},
            {"choices": [{"message": {"content": "План готов без служебной строки."}}]},
        ])
        road = lambda a, b, mode="car": {
            "from": a["name"], "to": b["name"], "ok": True,
            "coords": [[a["lon"], a["lat"]], [131.9, 43.0], [b["lon"], b["lat"]]],
            "distance_km": 150.0, "duration_min": 120, "source": "test",
        }
        with patch.object(real, "_osm_places", return_value=found), \
             patch.object(_roads, "route_leg", side_effect=road), \
             patch.object(llm, "call_llm", side_effect=lambda *args, **kwargs: next(responses)):
            result = llm.run_chat([{"role": "user", "content": "План поездки"}])

        self.assertEqual(result["answer"], "План готов без служебной строки.")
        self.assertEqual([m["name"] for m in result["map"]["markers"]],
                         ["Владивосток", "Артём", "Уссурийск", "Спасск-Дальний"])
        self.assertEqual([m["day"] for m in result["map"]["markers"]], [1, 1, 2, 2])
        self.assertEqual([(r["from"], r["to"]) for r in result["map"]["routes"]],
                         [("Владивосток", "Артём"), ("Артём", "Уссурийск"), ("Уссурийск", "Спасск-Дальний")])

    def test_ors_no_road_nearby_falls_back_to_osrm(self):
        from app.tools import _roads
        good = (1000, 60, [[131.0, 43.0], [131.1, 43.1]], "OSRM (openstreetmap.de)")
        with patch.object(_roads.config, "ORS_KEY", "k"), \
                patch.object(_roads, "_ors", side_effect=_roads.NoRoadRoute("ORS: рядом нет дороги")), \
                patch.object(_roads, "_fossgis", return_value=good):
            _roads._cache.clear()
            r = _roads.road_route((131.0, 43.0), (131.1, 43.1))
        self.assertEqual(r["source"], "OSRM (openstreetmap.de)")

    def test_all_sources_down_is_unavailable_not_no_road(self):
        from app.tools import _roads
        with patch.object(_roads.config, "ORS_KEY", None), \
                patch.object(_roads, "_fossgis", side_effect=RuntimeError("x")), \
                patch.object(_roads, "_osrm", side_effect=RuntimeError("y")):
            _roads._cache.clear()
            with self.assertRaises(_roads.RoutingUnavailable):
                _roads.road_route((131.0, 43.0), (131.5, 43.5))

    def test_walk_works_without_ors_key(self):
        from app.tools import _roads
        good = (500, 400, [[131.0, 43.0], [131.01, 43.01]], "OSRM (openstreetmap.de)")
        with patch.object(_roads.config, "ORS_KEY", None), patch.object(_roads, "_fossgis", return_value=good):
            _roads._cache.clear()
            self.assertEqual(_roads.road_route((131.0, 43.0), (131.01, 43.01), "walk")["distance_m"], 500)

    def test_osrm_noroute_and_thinning(self):
        from app.tools import _roads
        with patch.object(_roads, "http_json", return_value={"code": "NoRoute"}), patch.object(_roads.config, "ORS_KEY", None):
            _roads._cache.clear()
            with self.assertRaises(_roads.NoRoadRoute):
                _roads.road_route((131.0, 43.0), (131.5, 43.5))
        pts = [[131 + i / 1000, 43 + i / 1000] for i in range(2000)]
        thin = _roads._thin(pts)
        self.assertEqual((thin[0], thin[-1], len(thin)), (pts[0], pts[-1], _roads.MAX_POINTS_PER_LEG))


class RoadsDuringPlanning(unittest.TestCase):
    """Дороги строятся, пока модель составляет план (route_by_places), а карта в конце берёт готовые участки."""

    @staticmethod
    def _ok(a, b, mode="car"):
        return {"coords": [list(a), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.01], list(b)],
                "distance_m": 50000, "duration_s": 3000, "source": "OSRM", "snapped_m": 10}

    def _ctx_with_places(self, n=4):
        from app.tools import _ctx
        ctx = registry.new_context()
        return ctx, [_ctx.register_place(ctx, f"Точка {i}", 43 + i / 5, 131.9 + i / 5) for i in range(n)]

    def test_map_reuses_roads_built_by_tool(self):
        from app.tools import _roads
        ctx, ids = self._ctx_with_places(3)
        with patch.object(_roads, "road_route", side_effect=self._ok) as built:
            registry.run_tool("route_by_places", {"stops": ids}, ctx)
        self.assertEqual(built.call_count, 2)
        # сервисы «упали» — но карта уже не должна ничего считать: все участки построены во время планирования
        with patch.object(_roads, "road_route", side_effect=AssertionError("повторный расчёт")):
            m = registry.build_map_payload(ctx, [ids])
        self.assertEqual(len(m["routes"]), 2)
        self.assertTrue(all(not r.get("gap") and len(r["coords"]) > 2 for r in m["routes"]))

    def test_only_missing_cross_day_leg_is_built_at_the_end(self):
        from app.tools import _roads
        ctx, ids = self._ctx_with_places(4)
        with patch.object(_roads, "road_route", side_effect=self._ok):
            registry.run_tool("route_by_places", {"stops": ids[:2]}, ctx)
            registry.run_tool("route_by_places", {"stops": ids[2:]}, ctx)
        with patch.object(_roads, "road_route", side_effect=self._ok) as built:
            m = registry.build_map_payload(ctx, [ids[:2], ids[2:]])
        self.assertEqual(built.call_count, 1)                       # только переезд между днями
        self.assertEqual([(r["from"], r["to"]) for r in m["routes"]],
                         [("Точка 0", "Точка 1"), ("Точка 1", "Точка 2"), ("Точка 2", "Точка 3")])
        self.assertFalse(any(r.get("gap") for r in m["routes"]))

    def test_failed_leg_is_not_stored_and_gets_built_by_the_map(self):
        from app.tools import _roads
        ctx, ids = self._ctx_with_places(2)
        with patch.object(_roads, "RETRY_PAUSE_S", 0), \
                patch.object(_roads, "road_route", side_effect=_roads.RoutingUnavailable("down")):
            r = json.loads(registry.run_tool("route_by_places", {"stops": ids}, ctx))
        self.assertIn("warning", r)
        self.assertEqual(ctx["legs"], {})
        with patch.object(_roads, "road_route", side_effect=self._ok):
            m = registry.build_map_payload(ctx, [ids])
        self.assertFalse(m["routes"][0].get("gap"))

    def test_leg_is_retried_once_when_services_do_not_answer(self):
        from app.tools import _roads
        a, b = {"name": "A", "lat": 43.0, "lon": 131.9}, {"name": "B", "lat": 43.3, "lon": 132.1}
        with patch.object(_roads, "RETRY_PAUSE_S", 0), patch.object(
                _roads, "road_route", side_effect=[_roads.RoutingUnavailable("blip"), self._ok((131.9, 43.0), (132.1, 43.3))]) as rr:
            leg = _roads.route_leg(a, b)
        self.assertTrue(leg["ok"])
        self.assertEqual(rr.call_count, 2)
        with patch.object(_roads, "RETRY_PAUSE_S", 0), patch.object(_roads, "road_route", side_effect=_roads.NoRoadRoute("остров")) as rr:
            leg = _roads.route_leg(a, b)
        self.assertEqual((leg["ok"], leg["reason"], rr.call_count), (False, "no_road", 1))   # «нет дороги» не повторяем

    def test_prompt_asks_for_one_route_call_for_whole_trip(self):
        from app.agent import llm
        p = llm.system_prompt()
        self.assertIn("ОДИН раз на весь маршрут", p)
        self.assertIn("строка ROUTE", p)


class MapFrontendLayers(unittest.TestCase):
    """Регрессия: на карте были только маркеры, дороги не видны (см. комментарии в styles.css и map.js)."""
    STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

    def test_global_svg_rule_does_not_shrink_leaflet_overlay(self):
        css = (self.STATIC / "css" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("svg{width:20px;height:20px", css)              # иконки — как раньше
        self.assertIn(".leaflet-container svg{width:auto;height:auto}", css)   # слой линий Leaflet — своего размера

    def test_casing_lies_under_roads_by_pane_not_by_call_order(self):
        js = (self.STATIC / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("createPane('roadCasing')", js)
        self.assertIn("createPane('roadLine')", js)
        self.assertIn("pane: 'roadCasing'", js)
        self.assertNotRegex(js, r"\)\.bringTo(Back|Front)\(\)")          # порядок слоёв не через вызовы bringTo…(), а через pane
