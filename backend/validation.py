"""Валидация схемы КПСЦ Studio v2 (JSON version=3).

Не учитывает визуальные информационные линии как производственные зависимости.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque


class ModelError(ValueError):
    """Некорректная модель или нарушение ограничений симулятора."""


SETTINGS_LIMITS = {
    "target": (1, 160), "releaseUnits": (1, 200), "shiftMinutes": (1, 480),
    "taktSeconds": (10, 3600), "productionBatch": (1, 30),
    "transportBatch": (1, 20), "initialStock": (0, 30),
    "initialFlowWip": (0, 10), "logistCount": (1, 8),
    "handlingSeconds": (0, 240), "speed": (.1, 5),
    "returnMultiplier": (1, 3), "metersPerPixel": (.002, .15),
    "seed": (1, 1000000), "materialCost": (0, 10000000),
    "salePrice": (0, 10000000), "wageHour": (0, 100000),
    "logistWageHour": (0, 100000), "equipmentShift": (0, 100000000),
    "rentShift": (0, 100000000), "overheadShift": (0, 100000000),
}
INTEGER_SETTINGS = {"target", "releaseUnits", "productionBatch", "transportBatch", "initialStock", "initialFlowWip", "logistCount", "seed"}
OP_LIMITS = {
    "cycle": (1, 3600), "staff": (1, 8), "variation": (0, 50),
    "defect": (0, 35), "availability": (40, 100), "setup": (0, 3600),
}
ALLOWED_TYPES = {"operation", "warehouse", "customer", "qc"}
MAX_NODES, MAX_EDGES = 26, 90


def number(value, low, high, title):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError(f"{title}: ожидается число")
    if not math.isfinite(value) or value < low or value > high:
        raise ModelError(f"{title}: требуется значение от {low} до {high}")
    return value


def validate_model(model):
    """Возвращает подготовленный граф; не изменяет входной словарь."""
    if not isinstance(model, dict):
        raise ModelError("Модель должна быть JSON-объектом")
    nodes, edges, settings = model.get("nodes"), model.get("edges"), model.get("settings")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(settings, dict):
        raise ModelError("Нужны массивы nodes, edges и объект settings")
    if not 1 <= len(nodes) <= MAX_NODES or len(edges) > MAX_EDGES:
        raise ModelError("Допустимо 1–26 узлов и не более 90 материальных связей")
    by_id = {}
    operations = []
    types = defaultdict(int)
    for n in nodes:
        if not isinstance(n, dict):
            raise ModelError("Узел должен быть объектом")
        nid, nt = n.get("id"), n.get("type")
        if not isinstance(nid, str) or not nid or len(nid) > 80 or nid in by_id:
            raise ModelError("Пустой, слишком длинный или дублирующийся ID узла")
        if nt not in ALLOWED_TYPES:
            raise ModelError(f"Неизвестный тип узла {nt}")
        name = n.get("name", nid)
        if not isinstance(name, str) or len(name) > 160:
            raise ModelError(f"Узел {nid}: некорректное имя")
        number(n.get("x"), -100000, 100000, f"{nid}.x")
        number(n.get("y"), -100000, 100000, f"{nid}.y")
        types[nt] += 1
        by_id[nid] = n
        if nt in ("operation", "qc"):
            operations.append(n)
            for field, (low, high) in OP_LIMITS.items():
                number(n.get(field), low, high, f"{nid}.{field}")
            if int(n["staff"]) != n["staff"]:
                raise ModelError(f"{nid}.staff должен быть целым числом")
    if not operations or any(types[t] != 1 for t in ("warehouse", "qc", "customer")):
        raise ModelError("Нужны операции и ровно по одному складу, ОТК и заказчику")

    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    unique = set()
    for e in edges:
        if not isinstance(e, dict):
            raise ModelError("Связь должна быть объектом")
        a, b = e.get("from"), e.get("to")
        if a not in by_id or b not in by_id or a == b:
            raise ModelError("Связь с несуществующим объектом или с самим собой")
        if (a, b) in unique:
            raise ModelError(f"Дублирующая связь: {a} → {b}")
        unique.add((a, b))
        at, bt = by_id[a]["type"], by_id[b]["type"]
        if at == "customer" or bt == "warehouse" or (bt == "customer" and at != "qc") or (at == "qc" and bt != "customer"):
            raise ModelError(f"Запрещённая связь: {a} → {b}")
        outgoing[a].append(b)
        incoming[b].append(a)

    indegree = {nid: len(incoming[nid]) for nid in by_id}
    q = deque(nid for nid in by_id if indegree[nid] == 0)
    order = []
    while q:
        nid = q.popleft()
        order.append(nid)
        for other in outgoing[nid]:
            indegree[other] -= 1
            if indegree[other] == 0:
                q.append(other)
    if len(order) != len(by_id):
        raise ModelError("Циклические материальные зависимости не допускаются")

    warehouse = next(nid for nid, n in by_id.items() if n["type"] == "warehouse")
    qc = next(nid for nid, n in by_id.items() if n["type"] == "qc")
    customer = next(nid for nid, n in by_id.items() if n["type"] == "customer")
    if customer not in outgoing[qc]:
        raise ModelError("ОТК должна быть связана с заказчиком")
    def reach(start, neighbors):
        seen = set()
        pending = [start]
        while pending:
            cur = pending.pop()
            if cur in seen:
                continue
            seen.add(cur)
            pending.extend(neighbors[cur])
        return seen
    can_receive = reach(warehouse, outgoing)
    can_reach_qc = reach(qc, incoming)
    for op in operations:
        if op["id"] not in can_receive or op["id"] not in can_reach_qc or not incoming[op["id"]]:
            raise ModelError(f"Операция {op['id']} должна получать материал со склада и вести к ОТК")

    for key, (low, high) in SETTINGS_LIMITS.items():
        value = number(settings.get(key), low, high, f"settings.{key}")
        if key in INTEGER_SETTINGS and int(value) != value:
            raise ModelError(f"settings.{key} должен быть целым числом")
    if settings["releaseUnits"] < settings["target"]:
        raise ModelError("Запуск комплектов releaseUnits не может быть меньше плана target")
    for key in ("partialTransport", "layoutLogistics"):
        if not isinstance(settings.get(key), bool):
            raise ModelError(f"settings.{key} должен быть true/false")

    # Дополнительные метаданные КПСЦ не входят в граф производственных зависимостей.
    kpsc = model.get("kpsc") or {}
    if not isinstance(kpsc, dict):
        raise ModelError("kpsc должен быть объектом")
    floor = kpsc.get("floor", {})
    if not isinstance(floor, dict):
        raise ModelError("kpsc.floor должен быть объектом")
    for key, coords in floor.items():
        if key not in by_id:
            continue  # внешние метки могут иметь отдельные координаты
        if not isinstance(coords, list) or len(coords) != 2:
            raise ModelError(f"kpsc.floor.{key}: ожидается [x,y]")
        number(coords[0], 0, 18.4, f"kpsc.floor.{key}[0]")
        number(coords[1], 0, 6.15, f"kpsc.floor.{key}[1]")
    for key in ("infoLinks", "extras", "issues"):
        if key in kpsc and not isinstance(kpsc[key], list):
            raise ModelError(f"kpsc.{key}: ожидается массив")
    if "meta" in kpsc and not isinstance(kpsc["meta"], dict):
        raise ModelError("kpsc.meta должен быть объектом")
    return {"nodes": by_id, "ops": operations, "outgoing": outgoing, "incoming": incoming, "order": order,
            "warehouse": warehouse, "qc": qc, "customer": customer, "settings": settings}
