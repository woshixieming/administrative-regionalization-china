#!/usr/bin/env python3
"""备用通道：从 mumuy/data_location 提取 L1-L4。

该仓库整理自民政部 / 国务院公告，乡级数据更新至 2026 年，比统计局 2023 快照新。
仅在民政部接口（fetch_mca.py）不可用时使用，来源会在界面上明确标注为"第三方整理"。

数据文件：
  list.json        平铺字典 {6位码: 名称}，含省级 / 地级 / 县级
  code/{县码}.json  {9位乡码: 名称}
"""
import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw"

OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "data/cache/data_location",
                        help="data_location 数据目录，默认 data/cache/data_location")
    parser.add_argument("--as-of", default="2026-04", help="来源数据的实际截止日期")
    args = parser.parse_args()
    src = args.source_dir.expanduser().resolve()
    if not (src / "list.json").is_file() or not (src / "code").is_dir():
        parser.error("数据目录必须包含 list.json 和 code/；请准备数据或通过 --source-dir 指定")
    flat = json.loads((src / "list.json").read_text(encoding="utf-8"))

    provinces, cities, areas, streets = [], [], [], []
    for code, name in flat.items():
        if len(code) != 6:
            continue
        item = {"code": code + "000000", "name": name, "level": 0, "type": ""}
        if code.endswith("0000"):
            if code == "000000":
                continue
            item["level"] = 1
            provinces.append(item)
        elif code.endswith("00"):
            item["level"] = 2
            item["provinceCode"] = code[:2] + "0000000000"
            cities.append(item)
        else:
            item["level"] = 3
            cities_map_code = code[:4] + "00"
            item["cityCode"] = (
                cities_map_code + "000000"
                if cities_map_code in flat
                else code[:2] + "0000000000"
            )
            areas.append(item)

    # 省直辖县级（无地级）挂到虚拟地级容器
    city_codes = {c["code"] for c in cities}
    for p in provinces:
        prefix = p["code"][:2]
        if any(c["provinceCode"] == p["code"] for c in cities):
            continue
        own = [a for a in areas if a["code"][:2] == prefix]
        if not own:
            continue
        vcode = prefix + "0100000000"
        cities.append(
            {
                "code": vcode,
                "name": "市辖区",
                "level": 2,
                "type": "直辖市辖区",
                "provinceCode": p["code"],
                "virtual": True,
            }
        )
        city_codes.add(vcode)
        for a in own:
            a["cityCode"] = vcode

    for a in areas:
        if a.get("cityCode") not in city_codes:
            a["cityCode"] = a["code"][:2] + "0000000000"

    # 乡级：按县级分片文件读取
    code_dir = src / "code"
    for f in sorted(code_dir.glob("*.json")):
        area_code = f.stem + "000000"
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"读取乡级数据失败：{f.name}: {exc}") from exc
        for sc, name in obj.items():
            if len(sc) != 9:
                continue
            streets.append(
                {
                    "code": sc + "000",
                    "name": name,
                    "level": 4,
                    "type": "",
                    "areaCode": area_code,
                }
            )

    result = {
        "meta": {
            "source": "mumuy/data_location（整理自民政部 / 国务院公告，第三方整理）",
            "endpoint": "https://github.com/mumuy/data_location",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "officialNote": "民政部接口不可用时的备用通道；非一手官方接口数据",
            "levels": "1-4",
            "fallback": True,
            "asOf": args.as_of,
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
