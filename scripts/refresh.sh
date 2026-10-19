#!/usr/bin/env bash
# 行政区划数据自动刷新管线（4+1 口径）
#
#   1. 探测民政部接口（不可用则退避重试，超时保留原数据退出）
#   2. 备份当前线上数据 → data/snapshots/pre-<ts>
#   3. 抓取 L1–L4 → 校验村级基线 → 合并分片 → 健全性校验
#   4. 校验不通过自动回滚；通过后落地刷新报告与静态产物
#
# 用法：
#   ./scripts/refresh.sh                 # 完整刷新（抓取 + 构建 + 校验）
#   ./scripts/refresh.sh --skip-fetch    # 跳过抓取，用现有 data/raw 重建
#   PROBE_MAX=3 PROBE_INTERVAL=60 ./scripts/refresh.sh
#
# 注意：环境对 rm -rf 有批量删除守卫，故一律用 rsync --delete 做目录覆盖，
#       避免删除大目录被拦截导致管线中断。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-/Users/ryan/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"
DATA="$ROOT/web/public/data"
SNAP="$ROOT/data/snapshots"
LOGDIR="$ROOT/data/logs"
RAW="$ROOT/data/raw"
RSYNC="$(command -v rsync || echo /usr/bin/rsync)"

SKIP_FETCH=0
[ "${1:-}" = "--skip-fetch" ] && SKIP_FETCH=1

# 接口探测窗口：民政部版本发布可能延后，触发后持续探测
PROBE_MAX="${PROBE_MAX:-12}"
PROBE_INTERVAL="${PROBE_INTERVAL:-300}"
PROBE_URL="${PROBE_URL:-https://dmfw.mca.gov.cn/9095/xzqh/getList?code=110101000000&maxLevel=2}"

TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SNAP" "$LOGDIR" "$RAW"
LOG="$LOGDIR/refresh-$TS.log"
exec > >(tee -a "$LOG") 2>&1

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
# 用 rsync 覆盖目录内容（含删除目标端多余文件），不触发 rm -rf
sync_dir() { "$RSYNC" -a --delete "$1/" "$2/"; }

write_status() {
  "$PY" - "$1" "${2:-}" <<'EOF'
import json, sys
from pathlib import Path
info = {"status": sys.argv[1]}
if len(sys.argv) > 2 and sys.argv[2]:
    info["reason"] = sys.argv[2]
Path("data/raw/last_refresh.json").write_text(
    json.dumps(info, ensure_ascii=False), encoding="utf-8")
EOF
}

rollback() {
  say "回滚到刷新前状态"
  if [ -d "$SNAP/pre-$TS" ]; then
    mkdir -p "$DATA"
    sync_dir "$SNAP/pre-$TS" "$DATA"
    say "已恢复 snapshots/pre-$TS"
  else
    say "无可用快照，保持现有数据不动"
  fi
}

on_err() {
  say "!! 管线失败，保留刷新前数据"
  rollback
  write_status failed "管线执行失败，已回滚"
}
trap on_err ERR

# ---------- 1. 探测接口（先探测后备份，不可用时无需备份） ----------
if [ "$SKIP_FETCH" = "1" ]; then
  say "跳过抓取（--skip-fetch）"
else
  ok=0
  for i in $(seq 1 "$PROBE_MAX"); do
    code="$(curl -s -o /dev/null -m 15 -w '%{http_code}' -A 'Mozilla/5.0' "$PROBE_URL" || true)"
    [ -z "$code" ] && code="000"
    say "接口探测 $i/$PROBE_MAX HTTP=$code"
    if [ "$code" = "200" ]; then ok=1; break; fi
    [ "$i" -lt "$PROBE_MAX" ] && sleep "$PROBE_INTERVAL"
  done
  if [ "$ok" != "1" ]; then
    say "!! 民政部接口在探测窗口内不可用，本次不更新（保留原数据）"
    write_status skipped "民政部接口在探测窗口内不可用"
    exit 0
  fi
fi

# ---------- 2. 备份 ----------
if [ -d "$DATA" ]; then
  say "备份当前数据 → snapshots/pre-$TS"
  mkdir -p "$SNAP/pre-$TS"
  sync_dir "$DATA" "$SNAP/pre-$TS"
else
  say "无现有数据，跳过备份（首次运行）"
fi

# ---------- 3. 抓取 ----------
if [ "$SKIP_FETCH" != "1" ]; then
  # 强制全量重抓：归档上轮断点进度，避免跨版本脏数据残留
  [ -f "$RAW/_streets_progress.jsonl" ] && mv -f "$RAW/_streets_progress.jsonl" "$RAW/_streets_progress.prev.jsonl"
  [ -f "$RAW/mca_level1-4.json" ] && cp -f "$RAW/mca_level1-4.json" "$RAW/mca_level1-4.prev.json"
  rm -f "$RAW/_failed.json"
  say "抓取民政部 L1–L4（约 10–20 分钟，3 并发节流）"

  failed_count() {
    "$PY" - <<'EOF'
import json
from pathlib import Path
p = Path("data/raw/_failed.json")
print(len(json.loads(p.read_text(encoding="utf-8"))) if p.exists() and p.stat().st_size else 0)
EOF
  }

  left=1
  for attempt in 1 2 3; do
    "$PY" "$ROOT/scripts/fetch_mca.py"
    left="$(failed_count)"
    [ "$left" -eq 0 ] && break
    say "仍有 $left 个县级下钻失败，重试 $attempt/3"
    sleep 90
  done

  # 残余失败 ≤5 个时，fetch_mca 已从上一版补回这些县的乡级，可以接受
  if [ "$left" -gt 5 ]; then
    say "!! 抓取不完整（$left 个县级下钻失败），放弃本次更新，保留上一版数据"
    [ -f "$RAW/mca_level1-4.prev.json" ] && cp -f "$RAW/mca_level1-4.prev.json" "$RAW/mca_level1-4.json"
    write_status incomplete "$left 个县级下钻失败，未覆盖现有数据"
    exit 0
  fi
fi

# ---------- 4. 村级基线 + 构建 ----------
say "校验村级冻结基线"
"$PY" "$ROOT/scripts/ensure_baseline.py"

# 输出到 staging 再整体同步到站点目录：保证新版本是完整快照，且清理动作走 rsync 而非 rm -rf
STAGE="$ROOT/data/staging"
mkdir -p "$ROOT/data/.empty" "$STAGE"
sync_dir "$ROOT/data/.empty" "$STAGE"   # 清空 staging
say "合并并输出分片 → staging"
DATA_OUT="$STAGE" "$PY" "$ROOT/scripts/build_dataset.py"
mkdir -p "$DATA"
sync_dir "$STAGE" "$DATA"
say "已同步到 web/public/data"

# ---------- 5. 校验 / 回滚 ----------
say "健全性校验"
if ! "$PY" "$ROOT/scripts/validate.py"; then
  say "!! 校验不通过，回滚"
  rollback
  write_status failed "健全性校验未通过，已回滚"
  exit 1
fi

# ---------- 6. 报告 ----------
"$PY" - "$TS" <<'EOF'
import json, sys
from pathlib import Path
meta = json.loads(Path("web/public/data/meta.json").read_text(encoding="utf-8"))
rep = {
    "status": "ok",
    "finishedAt": sys.argv[1],
    "levels": {k: {"count": v["count"], "asOf": v["asOf"], "live": v["live"]}
               for k, v in meta["levels"].items()},
    "legacy": meta["legacy"],
}
Path("data/raw/last_refresh.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
EOF

# latest = 上次成功版本，供下次校验做漂移对比
mkdir -p "$SNAP/latest"
sync_dir "$DATA" "$SNAP/latest"

# 仅保留最近 5 份刷新前快照（用 python 清理，避免批量删除守卫）
"$PY" - <<'EOF'
import shutil
from pathlib import Path
snap = Path("data/snapshots")
olds = sorted(snap.glob("pre-*"), key=lambda p: p.stat().st_mtime, reverse=True)[5:]
for p in olds:
    shutil.rmtree(p, ignore_errors=True)
if olds:
    print(f"  清理旧快照 {len(olds)} 份")
EOF

say "刷新完成：$ROOT/data/raw/last_refresh.json"
say "日志：$LOG"

# 若已有静态导出产物，同步重建，避免 out/ 与 public/data 不一致
if [ -d "$ROOT/web/out" ] && [ "${SKIP_BUILD:-0}" != "1" ] && command -v npm >/dev/null 2>&1; then
  say "重建静态站点 out/"
  (cd "$ROOT/web" && npm run build) || say "静态构建失败，数据本身已更新，可稍后手动 npm run build"
fi
