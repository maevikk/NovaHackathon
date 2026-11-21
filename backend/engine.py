"""PROCESS LAB: дискретно-событийный симулятор графа операций.

Совместимые имена показателей с frontend/app.js (КПСЦ Studio v2).
Одна операция сборки требует по одному входному токену из каждого входящего
материального ребра. Информационные ребра не запускают производство.

Важно: начальный НЗП задаётся пользователем и может содержать готовые подсборки.
Это *демонстрационный сценарий*, а не фактический протокол фабрики.
"""
from __future__ import annotations

from collections import deque
import heapq
import math
from dataclasses import dataclass, field
from itertools import count
from .validation import ModelError, validate_model

DEFAULT_FLOOR = {
    "wh": [17.1, 3.3], "o1": [11.5, 4.6], "o2": [5.8, 1.7],
    "o3": [11.7, 1.7], "o4": [9.0, 1.8], "o5": [7.0, 4.6],
    "o6": [14.4, 1.6], "o7": [4.7, 4.6], "o8": [14.5, 4.6],
    "o9": [2.3, 1.7], "o10": [1.8, 4.6], "customer": [1.3, 2.8],
}


class XorShift32:
    """Та же псевдослучайная последовательность, что в автономном JS MVP."""
    def __init__(self, seed):
        self.value = int(seed) & 0xFFFFFFFF or 1

    def random(self):
        x = self.value
        x = (x ^ ((x << 13) & 0xFFFFFFFF)) & 0xFFFFFFFF
        x = (x ^ (x >> 17)) & 0xFFFFFFFF
        x = (x ^ ((x << 5) & 0xFFFFFFFF)) & 0xFFFFFFFF
        self.value = x
        return x / 4294967296.0


@dataclass
class Edge:
    source: str
    target: str
    dist: float
    travel: float
    ready: deque = field(default_factory=deque)
    pending: deque = field(default_factory=deque)
    transit: int = 0

    @property
    def id(self):
        return f"{self.source}>{self.target}"


def _dist(model, a, b):
    floor = (model.get("kpsc") or {}).get("floor") or {}
    x = floor.get(a, DEFAULT_FLOOR.get(a, [9.2, 3.0]))
    y = floor.get(b, DEFAULT_FLOOR.get(b, [9.2, 3.0]))
    return math.hypot(x[0] - y[0], x[1] - y[1])


def simulate(model):
    """Получает модель в формате сайта и возвращает совместимый result.

    Отсутствие данных у заказчика не заменяется фиктивными наблюдениями;
    их значения берутся из экспортированной пользователем демо-модели.
    """
    g = validate_model(model)
    ops, s, nodes = g["ops"], g["settings"], g["nodes"]
    horizon = s["shiftMinutes"] * 60
    rng = XorShift32(s["seed"])
    edges = {}
    arriving = {nid: [] for nid in nodes}
    leaving = {nid: [] for nid in nodes}
    for raw in model["edges"]:
        src, dst = raw["from"], raw["to"]
        d = _dist(model, src, dst)
        duration = s["handlingSeconds"] + (d / s["speed"] * s["returnMultiplier"] if s["layoutLogistics"] else 0)
        edge = Edge(src, dst, d, duration)
        edges[edge.id] = edge
        arriving[dst].append(edge)
        leaving[src].append(edge)

    op_by_id = {n["id"]: n for n in ops}
    stats = {}
    for n in ops:
        nid = n["id"]
        stats[nid] = {"id": nid, "name": n["name"], "started": 0, "processed": 0,
                      "good": 0, "scrap": 0, "busy": 0, "busySec": 0.0,
                      "waitingSec": 0.0, "waitSamples": 0, "maxQueue": 0,
                      "defectPct": n["defect"], "cycle": n["cycle"]}

    seq = count()
    events = []

    def event(t, etype, **fields):
        heapq.heappush(events, (t, next(seq), etype, fields))

    initial_wip = 0
    for edge in edges.values():
        if edge.source in op_by_id and edge.target in op_by_id:
            for _ in range(int(s["initialFlowWip"])):
                edge.ready.append({"birth": 0.0, "queuedAt": 0.0})
                initial_wip += 1

    for edge in leaving[g["warehouse"]]:
        if edge.target not in op_by_id:
            continue
        init = min(s["initialStock"], s["releaseUnits"])
        for i in range(int(s["releaseUnits"])):
            token = {"birth": 0.0, "queuedAt": 0.0}
            (edge.ready if i < init else edge.pending).append(token)

    logistic_busy = 0
    logistic_busy_sec = 0.0
    trips = 0
    distance = 0.0
    goods_ready = deque()
    accepted = finished = total_scrap = quality_scrap = processing = events_processed = 0
    flow_times = []
    series = [{"t": 0.0, "value": 0}]
    in_flight = 0

    def update_queue(nid):
        if nid in stats:
            total = sum(len(e.ready) for e in arriving[nid])
            stats[nid]["maxQueue"] = max(stats[nid]["maxQueue"], total)

    def start_at(nid, t):
        nonlocal processing
        n = op_by_id.get(nid)
        if n is None:
            return
        st, deps = stats[nid], arriving[nid]
        while (st["busy"] < n["staff"] and deps and all(e.ready for e in deps)
               and st["started"] < s["releaseUnits"]):
            inputs = [e.ready.popleft() for e in deps]
            birth = min(token["birth"] for token in inputs)
            st["waitSamples"] += 1
            st["waitingSec"] += sum(t - token["queuedAt"] for token in inputs) / len(inputs)
            st["started"] += 1
            st["busy"] += 1
            processing += 1
            deviation = (rng.random() * 2 - 1) * (n["variation"] / 100)
            duration = max(1.0, n["cycle"] * (1 + deviation) / (n["availability"] / 100)
                           + (n["setup"] if (st["started"] - 1) % s["productionBatch"] == 0 else 0))
            st["busySec"] += max(0.0, min(duration, horizon - t))
            event(t + duration, "done", nid=nid, birth=birth)
            update_queue(nid)

    def dispatch(t):
        nonlocal logistic_busy, logistic_busy_sec, trips, distance, in_flight
        while logistic_busy < s["logistCount"]:
            need = 1 if s["partialTransport"] else s["transportBatch"]
            eligible = [e for e in edges.values() if e.target != g["customer"] and len(e.pending) >= need]
            if not eligible:
                break
            eligible.sort(key=lambda e: (e.pending[0]["queuedAt"], e.id))
            edge = eligible[0]
            qty = min(len(edge.pending), s["transportBatch"])
            tokens = [edge.pending.popleft() for _ in range(qty)]
            logistic_busy += 1
            trips += 1
            distance += edge.dist * s["returnMultiplier"]
            logistic_busy_sec += max(0.0, min(edge.travel, horizon - t))
            in_flight += len(tokens)
            edge.transit += len(tokens)
            event(t + max(.02, edge.travel), "arrival", eid=edge.id, tokens=tokens)

    for op in ops:
        start_at(op["id"], 0.0)
    dispatch(0.0)
    t = s["taktSeconds"]
    while t <= horizon + 0.00001:
        event(t, "pickup")
        t += s["taktSeconds"]

    while events:
        t, _, event_type, payload = heapq.heappop(events)
        if t > horizon + 1e-8:
            break
        events_processed += 1
        if events_processed > 100000:
            raise ModelError("Слишком много событий в симуляции")
        if event_type == "done":
            nid, born = payload["nid"], payload["birth"]
            n, st = op_by_id[nid], stats[nid]
            st["busy"] -= 1
            processing -= 1
            st["processed"] += 1
            bad = rng.random() < n["defect"] / 100
            if bad:
                st["scrap"] += 1
                total_scrap += 1
                if n["type"] == "qc":
                    quality_scrap += 1
            else:
                st["good"] += 1
                if n["type"] == "qc":
                    finished += 1
                    goods_ready.append({"birth": born, "doneAt": t})
                else:
                    for edge in leaving[nid]:
                        edge.pending.append({"birth": born, "queuedAt": t})
            start_at(nid, t)
            dispatch(t)
        elif event_type == "arrival":
            edge = edges[payload["eid"]]
            logistic_busy -= 1
            in_flight -= len(payload["tokens"])
            edge.transit -= len(payload["tokens"])
            for token in payload["tokens"]:
                token["queuedAt"] = t
                edge.ready.append(token)
            update_queue(edge.target)
            start_at(edge.target, t)
            dispatch(t)
        elif event_type == "pickup":
            if goods_ready and accepted < s["target"]:
                done_token = goods_ready.popleft()
                accepted += 1
                flow_times.append(t - done_token["birth"])
            series.append({"t": t, "value": accepted})

    wip = processing + in_flight + len(goods_ready)
    stage_queue = 0
    waiting_transport = 0
    for edge in edges.values():
        if edge.source == g["warehouse"]:
            continue
        stage_queue += len(edge.ready)
        waiting_transport += len(edge.pending)
        wip += len(edge.ready) + len(edge.pending)

    shift_hours = horizon / 3600
    total_staff = sum(n["staff"] for n in ops)
    labor = (total_staff * s["wageHour"] + s["logistCount"] * s["logistWageHour"]) * shift_hours
    material = s["releaseUnits"] * s["materialCost"]
    fixed = s["equipmentShift"] + s["rentShift"] + s["overheadShift"]
    cost = labor + material + fixed
    revenue = accepted * s["salePrice"]
    profit = revenue - cost
    per_op = []
    for n in ops:
        st = stats[n["id"]]
        utilization = min(100.0, st["busySec"] / (n["staff"] * horizon) * 100)
        avg_waiting = st["waitingSec"] / st["waitSamples"] if st["waitSamples"] else 0.0
        per_op.append({**st, "utilization": utilization, "avgWaiting": avg_waiting,
                       "workers": n["staff"], "queueAtEnd": sum(len(e.ready) for e in arriving[n["id"]])})
    candidates = [p for p in per_op if p["id"] != g["qc"]]
    candidates.sort(key=lambda item: (-item["utilization"], -item["maxQueue"]))
    bottleneck = candidates[0] if candidates else None
    log_util = min(100.0, logistic_busy_sec / (horizon * s["logistCount"]) * 100)
    result = {
        "target": s["target"], "releaseUnits": s["releaseUnits"], "horizon": horizon,
        "initialWipCount": initial_wip, "accepted": accepted, "finished": finished,
        "waitingGoods": len(goods_ready), "throughputPerHour": accepted / shift_hours,
        "avgFlowSec": sum(flow_times) / len(flow_times) if flow_times else None,
        "flowTimes": flow_times, "wip": wip, "stageQueue": stage_queue,
        "waitingTransport": waiting_transport, "inFlight": in_flight,
        "processing": processing,
        "qualityPct": 100 * finished / (finished + quality_scrap) if finished + quality_scrap else None,
        "qualityScrap": quality_scrap, "totalScrap": total_scrap,
        "perOp": per_op, "series": series,
        "logistics": {"trips": trips, "distance": distance, "utilization": log_util,
                      "busySeconds": logistic_busy_sec, "waiting": waiting_transport},
        "financial": {"laborCost": labor, "materialCost": material, "fixedCosts": fixed,
                      "totalCost": cost, "revenue": revenue, "profit": profit,
                      "costPerUnit": cost / accepted if accepted else None,
                      "missedContribution": max(0, s["target"] - accepted) * max(0, s["salePrice"] - s["materialCost"])},
        "bottleneck": bottleneck,
        "runInfo": {"method": "дискретно-событийная модель", "seed": s["seed"],
                    "elapsedEvents": events_processed, "partials": s["partialTransport"]},
        "issues": [],
    }
    if accepted < s["target"]:
        result["issues"].append({"type": "critical", "title": "План выпуска не выполнен",
                                 "detail": f"Сдано {accepted} из {s['target']} изделий. Проверьте синхронизацию операций и логистику."})
    if bottleneck and bottleneck["utilization"] > 80:
        result["issues"].append({"type": "warning", "title": f"Высокая загрузка: {bottleneck['name']}",
                                 "detail": f"Загрузка {bottleneck['utilization']:.1f}%, максимум очереди {bottleneck['maxQueue']}."})
    if log_util > 70:
        result["issues"].append({"type": "warning", "title": "Перегрузка логиста",
                                 "detail": f"Занято {log_util:.1f}% времени, ожидает отправки {waiting_transport} единиц."})
    if wip > max(3, s["target"] * .3):
        result["issues"].append({"type": "warning", "title": "Незавершённое производство",
                                 "detail": f"На конец смены {wip} единиц в потоке (включая подсборки)."})
    if total_scrap:
        result["issues"].append({"type": "warning", "title": "Обнаружен брак",
                                 "detail": f"Отбраковано {total_scrap} подсборок/изделий; причины не моделируются."})
    if not result["issues"]:
        result["issues"].append({"type": "ok", "title": "Критичных отклонений не обнаружено",
                                 "detail": "Вывод только по демонстрационным параметрам."})
    return result
