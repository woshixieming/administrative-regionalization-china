#!/usr/bin/env python3
"""回退通道：从国家统计局 2023 快照生成与 fetch_mca.py 相同格式的 L1-L4。

仅在民政部接口不可用时使用。生成的数据会明确标注来源为 NBS 2023 快照，
正式数据请以 fetch_mca.py 的结果覆盖。
"""
import csv
import json
import time
from pathlib import Path

from ensure_baseline import TARGET, main as ensure_baseline

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw"
NBS2023_CSV = TARGET

OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if ensure_baseline() != 0:
        raise SystemExit("村级冻结基线校验失败，未生成回退数据")
    provinces, cities, areas, streets = [], [], [], []
    with open(NBS2023_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lv = int(r["level"])
            item = {"code": r["code"], "name": r["name"], "level": lv, "type": ""}
            if lv == 1:
                provinces.append(item)
            elif lv == 2:
                cities.append({**item, "provinceCode": r["parent_code"]})
            elif lv == 3:
                areas.append({**item, "cityCode": r["parent_code"]})
            elif lv == 4:
                streets.append({**item, "areaCode": r["parent_code"]})

    result = {
        "meta": {
            "source": "国家统计局 2023 年快照（回退通道，非民政部活源）",
            "endpoint": "local:data/cache/divisions-2023.csv",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "officialNote": "民政部接口暂不可用时的临时替代，村级与四级同源均为 2023-06-30",
            "levels": "1-4",
            "fallback": True,
        },
        "provinces": provinces,
        "cities": cities,
        "areas": areas,
        "streets": streets,
    }
    p = OUT / "mca_level1-4.json"
    p.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (OUT / "_failed.json").write_text("[]", encoding="utf-8")
    print(f"写入 {p}")
    print(f"省 {len(provinces)} 地 {len(cities)} 县 {len(areas)} 乡 {len(streets)}")


if __name__ == "__main__":
    main()
