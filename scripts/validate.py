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
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_OUT") or (ROOT / "web" / "public" / "data"))
PREV = ROOT / "data" / "snapshots" / "latest"

# 单级数量相对上一版允许的最大波动（行政区划变动远小于此，用于拦截残缺抓取）
MAX_DRIFT = 0.08
MIN_VILLAGE_ATTACH = 0.90
# 允许残余抓取失败的县级数：少量失败已由 fetch_mca 从上一版补回乡级
MAX_FAILED_AREAS = 5
MIN_SHARDS = {"l2": 30, "l3": 200, "l4": 200, "l5": 200}


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
    if set(levels) != {"1", "2", "3", "4", "5"}:
        fail("meta.json 必须包含完整的五级元数据")
        return 1

    # 0. 抓取完整性：存在未完成的县级下钻即为残缺数据（数量漂移可能仍在阈值内，必须单独拦）
    failed_p = ROOT / "data" / "raw" / "_failed.json"
    if failed_p.exists() and failed_p.stat().st_size:
        try:
            failed = json.loads(failed_p.read_text(encoding="utf-8"))
            if not isinstance(failed, list) or not all(isinstance(c, str) for c in failed):
                raise ValueError("失败清单格式错误")
            n = len(failed)
        except ValueError:
            fail("_failed.json 损坏，无法确认抓取完整性")
            return 1
        if n > MAX_FAILED_AREAS:
            ok &= fail(f"存在 {n} 个未完成的县级下钻（上限 {MAX_FAILED_AREAS}），数据不完整")
        elif n > 0:
            raw = json.loads((ROOT / "data/raw/mca_level1-4.json").read_text(encoding="utf-8"))
            recovered = {s["areaCode"] for s in raw["streets"] if s.get("carriedOver")}
            if set(failed) - recovered:
                ok &= fail("部分失败县级未从上一版补回乡级")
            else:
                print(f"  有 {n} 个县级下钻失败（已由上一版补回乡级，在允许范围内）")
    for d, lo in MIN_SHARDS.items():
        n = len(list((DATA / d).glob("*.json")))
        if n < lo:
            ok &= fail(f"{d} 分片仅 {n} 个（期望 ≥{lo}）")
    if not list(DATA.glob("provinces.json")):
        ok &= fail("provinces.json 缺失")

    # 2. 对实际分片计数，不能仅信任生成时写入的 meta.json。
    actual = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    actual["1"] = len(json.loads((DATA / "provinces.json").read_text(encoding="utf-8")))
    for level, folder in (("2", "l2"), ("3", "l3"), ("4", "l4")):
        for p in (DATA / folder).glob("*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = data if level != "4" else (r for group in data.values() for r in group)
            actual[level] += sum(1 for r in rows if not r.get("legacy") and (not r.get("virtual") or "datasetVersion" not in meta))
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

    # 4. 全量检查；缺失 l4 分片的村级也计入分母，不能跳过缺口。
    #    注意 l4 分片结构为 {县码: [乡级...]}，需展开为乡级 code 集合再比对
    attached = total = 0
    for shard in sorted((DATA / "l5").glob("*.json")):
        l4p = DATA / "l4" / shard.name
        l5 = json.loads(shard.read_text(encoding="utf-8"))
        l4 = json.loads(l4p.read_text(encoding="utf-8")) if l4p.exists() else {}
        known = {s["code"] for group in l4.values() for s in group}
        for street_code, vs in l5.items():
            total += len(vs)
            attached += sum(1 for v in vs if street_code in known and v["streetCode"] == street_code)
    actual["5"] = total
    for k, count in actual.items():
        if count != counts[k]:
            problems.append(f"L{k} 实际分片数量 {count} 与元数据 {counts[k]} 不一致")
    if total:
        rate = attached / total
        if rate < MIN_VILLAGE_ATTACH:
            problems.append(f"村级父层挂接率 {rate:.1%} < {MIN_VILLAGE_ATTACH:.0%}")
        else:
            print(f"  村级挂接率 {rate:.1%}")

    for p in problems:
        ok &= fail(p)

    # 新版本索引必须与展示数据一起发布，逐省验证父关系和重复键。
    if meta.get("searchVersion") is not None:
        manifest = json.loads((DATA / "search/manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") not in (1, 2) or manifest.get("generatedAt") != meta["generatedAt"] or manifest.get("datasetVersion") != meta.get("datasetVersion"):
            ok &= fail("搜索索引版本与展示数据不一致")
        provinces = json.loads((DATA / "provinces.json").read_text(encoding="utf-8"))
        if {p["code"] for p in manifest["provinces"]} != {p["code"] for p in provinces}:
            ok &= fail("搜索索引未覆盖全部省级")
        indexed = 0
        for province in manifest["provinces"]:
            if manifest["version"] == 1:
                rows = json.loads((DATA / "search" / f"{province['code']}.json").read_text(encoding="utf-8"))
            else:
                rows = json.loads((DATA / "search/upper" / f"{province['code']}.json").read_text())
                if len(rows) != province["upperCount"]:
                    ok &= fail("上四级索引数量不一致")
                for shard in province["shards"]:
                    if len(shard) != 4 or not shard.isdigit() or not province["code"].startswith(shard[:2]):
                        raise ValueError("村级分片无效")
                    rows.extend(json.loads((DATA / "search/villages" / f"{shard}.json").read_text()))
            if any(not isinstance(r, list) or len(r) not in (6, 7) or r[2] not in range(1, 6)
                   or not all(isinstance(r[i], str) for i in (0, 1, 3))
                   or r[4] not in (0, 1) or r[5] not in (0, 1) for r in rows):
                ok &= fail(f"{province['name']} 搜索索引格式异常")
                continue
            keys = {(r[2], r[0]) for r in rows}
            if len(keys) != len(rows) or len(rows) != province["count"]:
                ok &= fail(f"{province['name']} 搜索索引重复或数量不一致")
            if any((r[2] - 1, r[3]) not in keys for r in rows if r[2] > 1):
                ok &= fail(f"{province['name']} 搜索索引父层缺失")
            if sum(r[2] == 1 and r[0] == province["code"] for r in rows) != 1:
                ok &= fail(f"{province['name']} 搜索索引省级入口异常")
            indexed += len(rows)
        expected = sum(v.get("displayCount", v["count"]) for v in levels.values()) if meta.get("datasetVersion") else sum(actual.values()) + meta["legacy"]["areas"] + meta["legacy"]["streets"] + len(meta.get("directCities", []))
        if indexed != expected or indexed != manifest["total"]:
            ok &= fail("搜索索引总数与展示数据不一致")
        else:
            print(f"  搜索索引 {indexed} 条，父关系校验完成")

    if meta.get("datasetVersion"):
        frozen = DATA / "v" / meta["datasetVersion"]
        if not (frozen / "meta.json").is_file() or json.loads((frozen / "meta.json").read_text()) != meta:
            ok &= fail("固定版本数据缺失或与根元数据不一致")
        for name in ("provinces.json", "patches.json"):
            if not (frozen / name).is_file() or (DATA / name).read_bytes() != (frozen / name).read_bytes():
                ok &= fail(f"固定版本入口缺失或内容不一致：{name}")
        for path in (p for folder in ("l2", "l3", "l4", "l5", "search", "changes") for p in (DATA / folder).rglob("*.json")):
            other = frozen / path.relative_to(DATA)
            if not other.is_file() or path.read_bytes() != other.read_bytes():
                ok &= fail(f"固定版本分片缺失或内容不一致：{path.relative_to(DATA)}")
        summary = json.loads((DATA / "changes/summary.json").read_text())
        if summary["datasetVersion"] != meta["datasetVersion"]:
            ok &= fail("变更报告版本不一致")

    if ok:
        print("PASS  数据校验通过")
        print(
            "      " + " / ".join(f"L{k} {levels[k]['label']} {counts[k]}" for k in sorted(counts))
        )
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"FAIL  数据文件缺失、损坏或格式异常：{exc}", file=sys.stderr)
        sys.exit(1)
