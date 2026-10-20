"""按层级比较记录；村级分片逐个处理，避免同时载入两份全国快照。"""
import json
from collections import defaultdict
from pathlib import Path

KINDS = ("added", "removed", "renamed", "reparented", "statusChanged")
PARENT_KEYS = {2: "provinceCode", 3: "cityCode", 4: "areaCode", 5: "streetCode"}


def read_rows(path, level):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    rows = data if level < 4 else (row for group in data.values() for row in group)
    return {r["code"]: {"code": r["code"], "name": r["name"],
                         "parentCode": r.get(PARENT_KEYS.get(level), ""),
                         "legacy": bool(r.get("legacy")), "carriedOver": bool(r.get("carriedOver"))}
            for r in rows if not r.get("virtual")}


def compare_record(before, after):
    kinds = []
    if before["name"] != after["name"]:
        kinds.append("renamed")
    if before["parentCode"] != after["parentCode"]:
        kinds.append("reparented")
    if (before["legacy"], before["carriedOver"]) != (after["legacy"], after["carriedOver"]):
        kinds.append("statusChanged")
    return kinds


def write_changes(current, previous):
    from build_dataset import dump_json
    meta = json.loads((current / "meta.json").read_text())
    old_meta = json.loads((previous / "meta.json").read_text()) if (previous / "meta.json").exists() else None
    folder = current / "changes"
    folder.mkdir(exist_ok=True)
    counts = {kind: {str(level): 0 for level in range(1, 6)} for kind in KINDS}
    for level in range(1, 6):
        changes = defaultdict(list)
        removed, added = {}, {}
        paths = ["provinces.json"] if level == 1 else sorted({str(p.relative_to(base)) for base in (current, previous)
                           for p in (base / f"l{level}").glob("*.json")})
        if old_meta:
            for path in paths:
                before, after = read_rows(previous / path, level), read_rows(current / path, level)
                for code in before.keys() & after.keys():
                    for kind in compare_record(before[code], after[code]):
                        changes[kind].append({"code": code, "level": level, "before": before[code], "after": after[code]})
                removed.update({code: before[code] for code in before.keys() - after.keys()})
                added.update({code: after[code] for code in after.keys() - before.keys()})
            # 跨父分片移动仍识别为关系调整，而不是独立新增和撤销。
            for code in removed.keys() & added.keys():
                for kind in compare_record(removed[code], added[code]):
                    changes[kind].append({"code": code, "level": level, "before": removed[code], "after": added[code]})
            for code in removed.keys() - added.keys():
                changes["removed"].append({"code": code, "level": level, "before": removed[code], "after": None})
            for code in added.keys() - removed.keys():
                changes["added"].append({"code": code, "level": level, "before": None, "after": added[code]})
        for kind in KINDS:
            rows = sorted(changes[kind], key=lambda row: row["code"])
            counts[kind][str(level)] = len(rows)
            dump_json(folder / f"{kind}-{level}.json", rows)
    summary = {"datasetVersion": meta["datasetVersion"], "generatedAt": meta["generatedAt"], "initial": old_meta is None,
               "previousVersion": (old_meta.get("datasetVersion") or old_meta["generatedAt"]) if old_meta else None,
               "previousGeneratedAt": old_meta.get("generatedAt") if old_meta else None,
               "counts": counts, "totalEvents": sum(sum(levels.values()) for levels in counts.values()),
               "note": "比较数据记录变化，不等同于行政区划批复；首次无历史版本时不生成变更事件"}
    dump_json(folder / "summary.json", summary)
    return summary
