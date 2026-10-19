#!/usr/bin/env python3
"""合并民政部 L1-L4（活源）与统计局 2023 快照 L5（冻结基线），输出前端分片数据。

4+1 口径：
  - L1-L4 走民政部国家地名信息库（截止 2025-12-31）
  - L5 村级无官方活源，采用国家统计局 2023 年快照（截止 2023-06-30），冻结
  - 村级的父乡级若在最新版 L4 中已撤并，从 2023 快照补回并标记 legacy=true
"""
import csv
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
# 输出目录可用 DATA_OUT 覆盖；refresh.sh 会先输出到 staging 再 rsync 到站点目录
# （环境对批量删除有守卫，故本脚本不做 rmtree，只覆盖写）
OUT = Path(os.environ.get("DATA_OUT") or (ROOT / "web" / "public" / "data"))
# 村级冻结基线，由 scripts/ensure_baseline.py 保障存在
NBS2023_CSV = ROOT / "data" / "cache" / "divisions-2023.csv"

NBS_CUTOFF = "2023-06-30"
# 民政部每年 1 月发布上一年末全量，故活源截止日期恒为「上一年 12-31」
MCA_CUTOFF = os.environ.get("MCA_AS_OF") or f"{date.today().year - 1}-12-31"


def load_mca():
    return json.loads((RAW / "mca_level1-4.json").read_text(encoding="utf-8"))


def load_nbs(level_filter=None):
    """从 2023 快照读取指定层级的记录。"""
    rows = []
    with open(NBS2023_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if level_filter is None or int(row["level"]) in level_filter:
                rows.append(row)
    return rows


def shard_key(code: str) -> str:
    """分片键：县级代码前 4 位（直辖市区县同样适用）。"""
    return code[:4]


def main():
    # 不删除旧目录：环境对批量删除有守卫，清理交由 refresh.sh 用 rsync --delete 完成
    for d in ("l2", "l3", "l4", "l5"):
        (OUT / d).mkdir(parents=True, exist_ok=True)

    mca = load_mca()
    mca_meta = mca.get("meta", {})
    fallback = bool(mca_meta.get("fallback"))
    # 回退通道可自带 asOf；否则按是否为活源决定截止日期
    mca_cutoff = mca_meta.get("asOf") or (NBS_CUTOFF if fallback else MCA_CUTOFF)
    mca_source = mca_meta.get("source") or ("国家统计局 2023 快照（回退）" if fallback else "民政部")
    if fallback:
        print("!! 当前使用回退通道（非民政部活源），正式数据请重跑 scripts/fetch_mca.py")

    provinces = mca["provinces"]
    cities = mca["cities"]
    areas = mca["areas"]
    streets = mca["streets"]

    def dedup(items: list[dict]) -> list[dict]:
        seen, out = set(), []
        for x in items:
            if x["code"] in seen:
                continue
            seen.add(x["code"])
            out.append(x)
        return out

    provinces, cities, areas, streets = (
        dedup(provinces),
        dedup(cities),
        dedup(areas),
        dedup(streets),
    )

    street_by_code = {s["code"]: s for s in streets}
    area_by_code = {a["code"]: a for a in areas}

    print(f"民政部: 省 {len(provinces)} 地 {len(cities)} 县 {len(areas)} 乡 {len(streets)}")

    # ---- L5 村级：来自 2023 快照 ----
    print("读取 2023 快照村级 ...")
    villages = []
    for r in load_nbs({5}):
        villages.append(
            {"code": r["code"], "name": r["name"], "streetCode": r["parent_code"]}
        )
    print(f"村级 {len(villages)}")

    # 父乡级缺失时，用 2023 快照的 L4 补回（标记 legacy）
    legacy_streets = {}
    missing_parents = {v["streetCode"] for v in villages} - set(street_by_code)
    if missing_parents:
        print(f"村级父层在新版 L4 中缺失 {len(missing_parents)} 个，尝试从 2023 快照补回 ...")
        nbs4 = {r["code"]: r for r in load_nbs({4})}
        for code in missing_parents:
            r = nbs4.get(code)
            if not r:
                continue
            legacy_streets[code] = {
                "code": r["code"],
                "name": r["name"],
                "level": 4,
                "type": "",
                "areaCode": r["parent_code"],
                "legacy": True,
            }
        print(f"  补回 legacy 乡级 {len(legacy_streets)} 个")

    all_streets = list(streets) + list(legacy_streets.values())

    # legacy 乡级的父县级若也不存在，同样补回
    legacy_areas = {}
    area_codes = set(area_by_code)
    for st in legacy_streets.values():
        if st["areaCode"] not in area_codes:
            legacy_areas[st["areaCode"]] = None
    if legacy_areas:
        nbs3 = {r["code"]: r for r in load_nbs({3})}
        for code in list(legacy_areas):
            r = nbs3.get(code)
            if r:
                legacy_areas[code] = {
                    "code": r["code"],
                    "name": r["name"],
                    "level": 3,
                    "type": "",
                    "cityCode": r["parent_code"],
                    "legacy": True,
                }
        legacy_areas = {k: v for k, v in legacy_areas.items() if v}
        print(f"  补回 legacy 县级 {len(legacy_areas)} 个")

    all_areas = list(areas) + list(legacy_areas.values())

    # ---- 分片写出 ----
    l2 = defaultdict(list)
    for c in cities:
        l2[c["provinceCode"]].append(c)
    l3 = defaultdict(list)
    for a in all_areas:
        l3[a.get("cityCode") or "unknown"].append(a)
    l4 = defaultdict(lambda: defaultdict(list))
    for s in all_streets:
        l4[shard_key(s.get("areaCode") or s["code"])][s["areaCode"]].append(s)
    l5 = defaultdict(lambda: defaultdict(list))
    for v in villages:
        l5[shard_key(v["streetCode"])][v["streetCode"]].append(v)

    def dump(path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for k, v in l2.items():
        dump(OUT / "l2" / f"{k}.json", sorted(v, key=lambda x: x["code"]))
    for k, v in l3.items():
        dump(OUT / "l3" / f"{k}.json", sorted(v, key=lambda x: x["code"]))
    for k, v in l4.items():
        dump(OUT / "l4" / f"{k}.json", {kk: sorted(vv, key=lambda x: x["code"]) for kk, vv in v.items()})
    for k, v in l5.items():
        dump(OUT / "l5" / f"{k}.json", {kk: sorted(vv, key=lambda x: x["code"]) for kk, vv in v.items()})

    # ---- 搜索索引：按名称首字分片，使村级可被直接检索 ----
    # 条目压缩为 [名称, 代码, 层级]，首字分片后单次只加载一个分片
    print("生成搜索索引 ...")
    sidx = OUT / "sidx"
    sidx.mkdir(parents=True, exist_ok=True)

    def sidx_key(name: str) -> str:
        ch = name[0] if name else ""
        return f"u{ord(ch):x}" if ch else "_"

    groups: dict[str, list] = defaultdict(list)
    for s in all_streets:
        groups[sidx_key(s["name"])].append([s["name"], s["code"], 4])
    for v in villages:
        groups[sidx_key(v["name"])].append([v["name"], v["code"], 5])
    for k, rows in groups.items():
        dump(sidx / f"{k}.json", rows)

    # L1–L3 仅约 3 千条，合成单文件常驻
    top = (
        [[x["name"], x["code"], 1] for x in provinces]
        + [[x["name"], x["code"], 2] for x in cities]
        + [[x["name"], x["code"], 3] for x in all_areas]
    )
    dump(sidx / "top.json", top)
    n_entries = sum(len(r) for r in groups.values()) + len(top)
    dump(
        sidx / "_meta.json",
        {"shards": len(groups), "entries": n_entries, "top": len(top), "format": "[name, code, level]"},
    )
    sidx_mb = sum(f.stat().st_size for f in sidx.iterdir()) / 1024 / 1024
    print(f"  索引 {len(groups)} 个分片 / {n_entries} 条 / {sidx_mb:.1f} MB")

    meta = {
        "generatedAt": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "levels": {
            "1": {"label": "省级", "source": mca_source, "asOf": mca_cutoff, "count": len(provinces), "live": not fallback},
            "2": {"label": "地级", "source": mca_source, "asOf": mca_cutoff, "count": len(cities), "live": not fallback},
            "3": {"label": "县级", "source": mca_source, "asOf": mca_cutoff, "count": len(areas), "live": not fallback},
            "4": {"label": "乡级", "source": mca_source, "asOf": mca_cutoff, "count": len(streets), "live": not fallback},
            "5": {"label": "村级", "source": "国家统计局（已停发）", "asOf": NBS_CUTOFF, "count": len(villages), "live": False},
        },
        "legacy": {
            "streets": len(legacy_streets),
            "areas": len(legacy_areas),
            "note": "仅为挂载 2023 村级数据而保留的历史层级，非当前建制",
        },
        "search": {"shards": len(groups), "entries": n_entries},
        "sources": [
            {
                "name": (mca_meta.get("source") or "中华人民共和国民政部 · 国家地名信息库"),
                "url": "https://dmfw.mca.gov.cn",
                "levels": "1-4",
                "asOf": mca_cutoff,
            },
            {"name": "国家统计局 · 统计用区划代码（2023 版，已停发）", "url": "https://www.stats.gov.cn", "levels": "5", "asOf": NBS_CUTOFF},
        ],
    }
    dump(OUT / "meta.json", meta)
    dump(OUT / "provinces.json", sorted(provinces, key=lambda x: x["code"]))

    total = sum((OUT / d).stat().st_size for d in ["provinces.json", "meta.json"])
    for d in ["l2", "l3", "l4", "l5"]:
        total += sum(f.stat().st_size for f in (OUT / d).iterdir())
    print(f"输出分片: l2={len(l2)} l3={len(l3)} l4={len(l4)} l5={len(l5)}  合计 {total/1024/1024:.1f} MB")
    print(json.dumps(meta["levels"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
