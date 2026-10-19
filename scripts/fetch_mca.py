#!/usr/bin/env python3
"""抓取民政部国家地名信息库行政区划数据（省级 -> 乡级，L1-L4）。

数据源: https://dmfw.mca.gov.cn/9095/xzqh/getList
注意: 根节点 maxLevel 只返回到县级，乡级必须按县级 code 逐级下钻。
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import urllib.request
import urllib.error

BASE = "https://dmfw.mca.gov.cn/9095/xzqh/getList"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# 民政部接口对并发敏感，超过阈值会返回 502；保持低速 + 长退避
MAX_WORKERS = 3
RETRY = 5
THROTTLE = 0.35


def fetch(code: str, max_level: int):
    """带节流与退避重试地请求单个节点。返回 data 节点，失败返回 None。"""
    url = f"{BASE}?code={code}&maxLevel={max_level}"
    last = None
    for i in range(RETRY):
        try:
            time.sleep(THROTTLE)
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
            data = body.get("data")
            if not data:
                return None
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    print(f"  ! 失败 code={code} err={last}", file=sys.stderr)
    return None


def norm(node):
    return {
        "code": node["code"],
        "name": node["name"],
        "level": node["level"],
        "type": node.get("type") or "",
    }


def main():
    t0 = time.time()
    print("[1/3] 抓取省/地/县三级 ...")
    root = fetch("", 3)
    if not root:
        print("根节点抓取失败", file=sys.stderr)
        sys.exit(1)

    provinces, cities, areas = [], [], []

    def walk(node, parent_code=None):
        for ch in node.get("children") or []:
            item = norm(ch)
            lv = item["level"]
            if lv == 1:
                provinces.append(item)
            elif lv == 2:
                cities.append({**item, "provinceCode": parent_code})
            elif lv == 3:
                areas.append({**item, "cityCode": parent_code})
            if lv in (1, 2):
                walk(ch, item["code"])

    walk(root)
    print(f"  省 {len(provinces)} / 地 {len(cities)} / 县 {len(areas)}")

    # 直辖市的县级节点挂在省下（level 跳跃 1 -> 3），补 cityCode
    # 直辖市在民政部口径下是 level1 -> level3 直跳，没有地级层。
    # 补一个虚拟地级容器（沿用统计局"市辖区"口径），保证五级链路统一。
    city_codes = {c["code"] for c in cities}
    for p in provinces:
        if any(c["provinceCode"] == p["code"] for c in cities):
            continue
        prefix = p["code"][:2]
        # 港澳台等在民政部口径下无下级建制，不补容器；code 前缀非数字时同样跳过
        if not prefix.isdigit():
            continue
        own = [a for a in areas if a["code"][:2] == prefix]
        if not own:
            continue
        vcode = prefix + "01" + "0" * 8
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
        n = 0
        for a in areas:
            if a.get("cityCode") == p["code"] or (
                a["code"][:2] == p["code"][:2] and a["level"] == 3
                and a.get("cityCode") not in city_codes
            ):
                a["cityCode"] = vcode
                n += 1
        print(f"  直辖市 {p['name']} 补虚拟地级 {vcode}，挂载 {n} 个县级")

    for a in areas:
        if not a.get("cityCode") or a["cityCode"] not in city_codes:
            a["cityCode"] = a["code"][:2] + "0000000000"

    # 断点续传：已完成的下钻结果逐行落盘，重跑时跳过
    progress = OUT / "_streets_progress.jsonl"
    streets = []
    done_codes = set()
    if progress.exists():
        for line in progress.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            if "__empty__" in o:
                done_codes.add(o["__empty__"])
            else:
                streets.append(o)
    done_codes |= {s["areaCode"] for s in streets}

    print(f"[2/3] 下钻乡级（{len(areas)} 个县级节点，已完成 {len(done_codes)}）...")
    targets = [a["code"] for a in areas if a["code"] not in done_codes]
    done = 0
    failed: list[str] = []

    if targets:
        with progress.open("a", encoding="utf-8") as fp, ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as ex:
            futs = {ex.submit(fetch, c, 2): c for c in targets}
            for fut in as_completed(futs):
                code = futs[fut]
                data = fut.result()
                done += 1
                if data is None:
                    failed.append(code)
                    continue
                rows = [
                    {**norm(ch), "areaCode": code}
                    for ch in (data.get("children") or [])
                    if ch["level"] == 4
                ]
                for r in rows:
                    fp.write(json.dumps(r, ensure_ascii=False) + "\n")
                if not rows:
                    # 该县确无乡级，写空标记避免反复重试
                    fp.write(json.dumps({"__empty__": code}) + "\n")
                streets.extend(rows)
                if done % 200 == 0:
                    fp.flush()
                    print(f"  {done}/{len(targets)}  乡级累计 {len(streets)}", flush=True)
            fp.flush()
        if failed:
            (OUT / "_failed.json").write_text(
                json.dumps(failed, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  失败 {len(failed)} 个，已记录 data/raw/_failed.json（重跑会继续）")

    print(f"  乡级 {len(streets)}，耗时 {time.time() - t0:.0f}s")

    # 直辖市/省直管：县级节点本身也是乡级父，补一层
    # 东莞、中山、儋州等地级市不设县级，街道直挂市下：补一个与市同码的虚拟县级容器
    referenced = {a["cityCode"] for a in areas}
    direct = [c for c in cities if c["code"] not in referenced]
    if direct:
        print(f"[2.5/3] 直管街道的地级市 {len(direct)} 个，下钻并补虚拟县级容器 ...")
        for c in direct:
            data = fetch(c["code"], 2)
            if not data:
                continue
            subs = [norm(ch) for ch in (data.get("children") or []) if ch["level"] == 4]
            if not subs:
                continue
            areas.append(
                {
                    "code": c["code"],
                    "name": c["name"],
                    "level": 3,
                    "type": "直管街道/镇",
                    "cityCode": c["code"],
                    "virtual": True,
                }
            )
            for s in subs:
                streets.append({**s, "areaCode": c["code"]})
            print(f"  {c['name']} 补 {len(subs)} 个乡级")

    # 残余失败的县：从上一版补回其乡级，避免这些县在站点上出现空列表
    # （失败多为并发下的瞬时故障，接口对该 code 单独请求通常是正常的）
    prev_p = OUT / "mca_level1-4.prev.json"
    if failed and prev_p.exists():
        try:
            prev_streets = json.loads(prev_p.read_text(encoding="utf-8"))["streets"]
        except (ValueError, KeyError):
            prev_streets = []
        by_area: dict[str, list] = {}
        for s in prev_streets:
            by_area.setdefault(s.get("areaCode") or "", []).append(s)
        have = {s["code"] for s in streets}
        rescued = 0
        for code in failed:
            for s in by_area.get(code, []):
                if s["code"] in have:
                    continue
                streets.append({**s, "carriedOver": True})
                have.add(s["code"])
                rescued += 1
        if rescued:
            print(f"[2.8/3] {len(failed)} 个县本次抓取失败，从上一版补回 {rescued} 个乡级")

    print(f"[3/3] 汇总：省 {len(provinces)} 地 {len(cities)} 县 {len(areas)} 乡 {len(streets)}")

    def dedup(items):
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

    # 民政部每年 1 月发布上一年末全量
    as_of = os.environ.get("MCA_AS_OF") or f"{date.today().year - 1}-12-31"
    result = {
        "meta": {
            "source": "中华人民共和国民政部 · 国家地名信息库",
            "endpoint": BASE,
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "asOf": as_of,
            "officialNote": f"本库发布全国省、地、县、乡四级行政区划代码；数据截止 {as_of}",
            "levels": "1-4",
        },
        "provinces": provinces,
        "cities": cities,
        "areas": areas,
        "streets": streets,
    }
    p = OUT / "mca_level1-4.json"
    p.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"[3/3] 写入 {p} ({p.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"    省 {len(provinces)} 地 {len(cities)} 县 {len(areas)} 乡 {len(streets)}")


if __name__ == "__main__":
    main()
