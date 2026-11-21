"""Расширенные показатели КПСЦ, отдельно от имитационной модели.

Оценки НЕ являются фактическими хронометражами и не смешиваются с
дискретно-событийным прогнозом. Структура сохраняет совместимость фронтенда.
"""
from __future__ import annotations
from .validation import validate_model
from .engine import _dist


def analyze(model, result=None):
    graph = validate_model(model)
    settings = graph["settings"]
    nodes = graph["nodes"]
    floor = (model.get("kpsc") or {}).get("floor") or {}
    kmeta = (model.get("kpsc") or {}).get("meta") or {}
    path = {nid: 0.0 for nid in graph["order"]}
    ancestors = {}
    # Оценочный критический путь (без ожиданий, вариации, брака и переналадки).
    for nid in graph["order"]:
        n = nodes[nid]
        choices = []
        for parent in graph["incoming"][nid]:
            travel = settings["handlingSeconds"] + (
                _dist(model, parent, nid) / settings["speed"] * settings["returnMultiplier"]
                if settings["layoutLogistics"] else 0.0)
            if nid == graph["customer"]:
                travel = 0.0  # самовывоз заказчиком без логиста
            choices.append((path[parent] + travel, parent))
        before, ancestor = max(choices, default=(0.0, None))
        ancestors[nid] = ancestor
        duration = (n.get("cycle", 0) / (n.get("availability", 100) / 100.0)
                    if n["type"] in ("operation", "qc") else 0.0)
        path[nid] = before + duration

    endpoint = graph["customer"]
    critical_ids = []
    cur = endpoint
    while cur is not None:
        critical_ids.append(cur)
        cur = ancestors[cur]
    critical_ids.reverse()
    total = path[endpoint]
    value_added = sum(nodes[nid].get("cycle", 0) for nid in critical_ids
                      if nid in kmeta and kmeta[nid].get("addsValue", False))
    theoretical = []
    for n in graph["ops"]:
        # грубый показатель отдельно взятой операции; не является гарантией выпуска всего потока
        hourly = 3600 * n["staff"] / n["cycle"] * n["availability"] / 100
        meta = kmeta.get(n["id"]) or {}
        theoretical.append({
            "id": n["id"], "name": n["name"], "theoreticalHourlyCapacity": hourly,
            "processBatchDocumented": meta.get("batch"), "equipmentDocumented": meta.get("equipment"),
            "downtimeMinutesDocumented": meta.get("downtimeMinutes"),
            "processCostDocumented": meta.get("processCost"),
        })
    data_quality = [
        "Примерные времена циклов/стоимость исходной фабрики — демоданные, пока нет фактических замеров.",
        "Критический путь — структурная оценка без очередей и ожиданий; реальный ВПП может быть больше.",
        "Документируемые настройки kpsc.meta (запасы, простои, затраты операции) пока не управляют симуляцией.",
        "Информационные стрелки kpsc.infoLinks не являются материальными зависимостями.",
        "Количество промежуточных подсборок initialFlowWip — условная начальная конфигурация.",
        "Логистика использует условные прямые расстояния, не реальные маршруты по цеху.",
        "Экономика: материалы начисляются на все запущенные комплекты, выручка — на принятые изделия.",
    ]
    return {
        "taktSeconds": settings["taktSeconds"],
        "theoreticalCriticalPathSec": total,
        "criticalPathNodes": critical_ids,
        "valueAddedSecOnCriticalPath": value_added,
        "valueAddedRatioApproxPct": 100 * value_added / total if total else None,
        "standaloneOperationCapacity": theoretical,
        "targetShiftOutput": settings["target"],
        "actualModelOutput": result["accepted"] if result else None,
        "modelOutputGap": result["accepted"] - settings["target"] if result else None,
        "evidence": "demo_synthetic_not_factory_measurements",
        "dataQualityNotes": data_quality,
    }


def compare(base_model, candidate_model):
    from .engine import simulate
    b = simulate(base_model)
    c = simulate(candidate_model)
    metrics = {
        "accepted": (b["accepted"], c["accepted"]),
        "wip": (b["wip"], c["wip"]),
        "avgFlowSec": (b["avgFlowSec"], c["avgFlowSec"]),
        "throughputPerHour": (b["throughputPerHour"], c["throughputPerHour"]),
        "logisticsTrips": (b["logistics"]["trips"], c["logistics"]["trips"]),
        "logisticsDistance": (b["logistics"]["distance"], c["logistics"]["distance"]),
        "profit": (b["financial"]["profit"], c["financial"]["profit"]),
        "totalCost": (b["financial"]["totalCost"], c["financial"]["totalCost"]),
    }
    delta = {name: {"before": before, "after": after,
                    "change": (after - before if before is not None and after is not None else None)}
             for name, (before, after) in metrics.items()}
    return {"baseline": b, "candidate": c, "delta": delta,
            "analysis": {"baseline": analyze(base_model, b), "candidate": analyze(candidate_model, c)}}
