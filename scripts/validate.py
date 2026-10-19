#!/usr/bin/env python3
"""对刚生成的站点数据做健全性校验；不通过则调用方应回滚到上一版快照。

校验项：
  1. 必需文件与分片数量
  2. 各级数量不得为 0，且与上一版相比波动在阈值内（防止接口半途限流导致残缺）
  3. 村级为冻结基线，条数必须与上一版完全一致
  4. 村级父层挂接率（能挂到现有乡级的比例）
退出码：0 通过 / 1 失败
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "public" / "data"
PREV = ROOT / "data" / "snapshots" / "latest"

# 单级数量相对上一版允许的最大波动（行政区划变动远小于此，用于拦截残缺抓取）
MAX_DRIFT = 0.08
MIN_VILLAGE_ATTACH = 0.90
# 允许残余抓取失败的县级数：少量失败已由 fetch_mca 从上一版补回乡级
MAX_FAILED_AREAS = 5


def fail(msg):
    print(f"FAIL  {msg}", file=sys.stderr)
    return False


def main() -> int:
    ok = True
    problems = []

    meta_p = DATA / "meta.json"
    if not meta_p.exists():
        print("FAIL  meta.json 不存在", file=sys.stderr)
        return 1
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    levels = meta["levels"]

    # 0. 抓取完整性：存在未完成的县级下钻即为残缺数据（数量漂移可能仍在阈值内，必须单独拦）
    failed_p = ROOT / "data" / "raw" / "_failed.json"
    if failed_p.exists() and failed_p.stat().st_size:
        try:
            n = len(json.loads(failed_p.read_text(encoding="utf-8")))
        except ValueError:
            n = -1
        if n > MAX_FAILED_AREAS:
            ok &= fail(f"存在 {n} 个未完成的县级下钻（上限 {MAX_FAILED_AREAS}），数据不完整")
        elif n > 0:
            print(f"  有 {n} 个县级下钻失败（已由上一版补回乡级，在允许范围内）")
    min_shards = {"l2": 30, "l3": 200, "l4": 200, "l5": 200}
    for d, lo in min_shards.items():
        n = len(list((DATA / d).iterdir())) if (DATA / d).exists() else 0
        if n < lo:
            ok &= fail(f"{d} 分片仅 {n} 个（期望 ≥{lo}）")

    # 搜索索引：缺失会导致搜索功能静默失效
    sidx = DATA / "sidx"
    n_shards = len(list(sidx.iterdir())) if sidx.exists() else 0
    if n_shards < 100:
        ok &= fail(f"sidx 分片仅 {n_shards} 个（期望 ≥100）")
    sidx_top = sidx / "top.json"
    if not sidx_top.exists():
        ok &= fail("sidx/top.json 缺失（搜索索引未生成）")
    else:
        n = len(json.loads(sidx_top.read_text(encoding="utf-8")))
        if n < 1000:
            ok &= fail(f"搜索索引 L1–L3 仅 {n} 条（期望 >1000）")
    if not list(DATA.glob("provinces.json")):
        ok &= fail("provinces.json 缺失")

    # 2. 各级数量
    counts = {k: v["count"] for k, v in levels.items()}
    for k, c in counts.items():
        if c <= 0:
            ok &= fail(f"L{k} {levels[k]['label']} 数量为 0")

    # 3. 与上一版对比
    prev_meta_p = PREV / "meta.json"
    if prev_meta_p.exists():
        prev = json.loads(prev_meta_p.read_text(encoding="utf-8"))["levels"]
        for k in ("1", "2", "3", "4"):
            old = prev[k]["count"]
            new = counts[k]
            if old <= 0:
                continue
            drift = abs(new - old) / old
            if drift > MAX_DRIFT:
                problems.append(
                    f"L{k} {levels[k]['label']} 数量 {old} → {new}（波动 {drift:.1%} > {MAX_DRIFT:.0%}）"
                )
        # 村级冻结：必须完全一致
        if prev["5"]["count"] != counts["5"]:
            problems.append(
                f"村级基线数量变化 {prev['5']['count']} → {counts['5']}（村级为冻结基线，不应变动）"
            )
        if levels.get("5", {}).get("asOf") != prev["5"]["asOf"]:
            problems.append("村级截止日期发生变化")
    else:
        print("  无上一版快照可比，跳过漂移校验（首次运行）")

    # 4. 村级挂接率：抽样分片，检查每个村级的父乡级是否真实存在于同级 l4 分片中
    #    注意 l4 分片结构为 {县码: [乡级...]}，需展开为乡级 code 集合再比对
    attached = total = 0
    for shard in sorted((DATA / "l5").iterdir())[:150]:
        l4p = DATA / "l4" / shard.name
        if not l4p.exists():
            continue
        l5 = json.loads(shard.read_text(encoding="utf-8"))
        l4 = json.loads(l4p.read_text(encoding="utf-8"))
        known = {s["code"] for group in l4.values() for s in group}
        for street_code, vs in l5.items():
            total += len(vs)
            if street_code in known:
                attached += len(vs)
    if total:
        rate = attached / total
        if rate < MIN_VILLAGE_ATTACH:
            problems.append(f"村级父层挂接率 {rate:.1%} < {MIN_VILLAGE_ATTACH:.0%}")
        else:
            print(f"  村级挂接率 {rate:.1%}")

    for p in problems:
        ok &= fail(p)

    if ok:
        print("PASS  数据校验通过")
        print(
            "      " + " / ".join(f"L{k} {levels[k]['label']} {counts[k]}" for k in sorted(counts))
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
