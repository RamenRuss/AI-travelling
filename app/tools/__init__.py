"""Инструменты агента. Точка входа для агента — registry.py (там же схемы, вызов, подписи шагов, данные карты).

registry.py   — единый реестр: сам находит модули с TOOLS/TOOL_FUNCS (docs/ADD_TOOL.md)
real.py       — РЕАЛЬНЫЕ тулы: search_places, get_weather, get_route (внешние API)
mock_data.py  — МОК-тулы: find_fuel, find_lodging, find_transport (демо-данные app/data/mock)
_template.py  — шаблон нового инструмента (реестр пропускает файлы с «_»)
"""
