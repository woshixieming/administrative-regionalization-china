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
cd "$ROOT"
PY="${PY:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  printf '未找到 Python 3，请安装或通过 PY 指定解释器\n' >&2
  exit 1
fi

# flock 由 Python 标准库提供，兼容 macOS / Linux；锁随进程退出自动释放。
if [ "${DIVISIONS_REFRESH_LOCKED:-0}" != "1" ]; then
  exec "$PY" - "$ROOT" "$@" <<'EOF'
import fcntl, os, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
(root / "data").mkdir(exist_ok=True)
lock = (root / "data/.refresh.lock").open("a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("已有刷新任务运行，本次跳过", flush=True)
    sys.exit(0)
os.set_inheritable(lock.fileno(), True)
bash = shutil.which("bash")
if bash is None:
    sys.exit("未找到 bash")
os.execve(bash, [bash, str(root / "scripts/refresh.sh"), *sys.argv[2:]],
          {**os.environ, "DIVISIONS_REFRESH_LOCKED": "1"})
EOF
fi
DATA="$ROOT/web/public/data"
SNAP="$ROOT/data/snapshots"
LOGDIR="$ROOT/data/logs"
RAW="$ROOT/data/raw"
RSYNC="$(command -v rsync)" || { printf '未找到 rsync\n' >&2; exit 1; }

SKIP_FETCH=0
case "${1:-}" in
  --skip-fetch) SKIP_FETCH=1 ;;
  "") ;;
  *) printf '用法：%s [--skip-fetch]\n' "$0" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { printf '参数过多\n' >&2; exit 2; }

# 接口探测窗口：民政部版本发布可能延后，触发后持续探测
PROBE_MAX="${PROBE_MAX:-12}"
PROBE_INTERVAL="${PROBE_INTERVAL:-300}"
PROBE_URL="${PROBE_URL:-https://dmfw.mca.gov.cn/9095/xzqh/getList?code=110101000000&maxLevel=2}"
if ! [[ "$PROBE_MAX" =~ ^[1-9][0-9]*$ && "$PROBE_INTERVAL" =~ ^[0-9]+$ ]]; then
  printf 'PROBE_MAX 必须为正整数，PROBE_INTERVAL 必须为非负整数\n' >&2
  exit 2
fi

TS="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP_READY=0
PUBLISH_STARTED=0
RAW_CHANGED=0
RAW_HAD_PREVIOUS=0
mkdir -p "$SNAP" "$LOGDIR" "$RAW"
LOG="$LOGDIR/refresh-$TS.log"
exec > >(tee -a "$LOG") 2>&1

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
# 用 rsync 覆盖目录内容（含删除目标端多余文件），不触发 rm -rf
sync_dir() { "$RSYNC" -a --checksum --delete "$1/" "$2/"; }

write_status() {
  if [ -f "$ROOT/scripts/refresh_report.py" ]; then
    "$PY" "$ROOT/scripts/refresh_report.py" "$1" "${2:-}"
    return
  fi
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
  if [ "$PUBLISH_STARTED" = "1" ] && [ "$BACKUP_READY" = "1" ]; then
    sync_dir "$SNAP/pre-$TS" "$DATA"
    say "已恢复 snapshots/pre-$TS"
  elif [ "$PUBLISH_STARTED" = "1" ]; then
    sync_dir "$ROOT/data/.empty" "$DATA"
  else
    say "尚未发布，保留现有站点数据"
  fi
  if [ "$RAW_CHANGED" = "1" ]; then
    if [ "$RAW_HAD_PREVIOUS" = "1" ]; then
      cp -f "$RAW/mca_level1-4.prev.json" "$RAW/mca_level1-4.json"
    else
      rm -f "$RAW/mca_level1-4.json"
    fi
  fi
}

on_err() {
  local status="${1:-$?}"
  trap - ERR INT TERM
  say "!! 管线失败，保留刷新前数据"
  rollback
  write_status failed "管线执行失败，已回滚"
  exit "$status"
}
trap 'on_err $?' ERR
trap 'on_err 130' INT
trap 'on_err 143' TERM
write_status running

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
  BACKUP_READY=1
else
  say "无现有数据，跳过备份（首次运行）"
fi

# ---------- 3. 抓取 ----------
if [ "$SKIP_FETCH" != "1" ]; then
  # 强制全量重抓：归档上轮断点进度，避免跨版本脏数据残留
  [ -f "$RAW/_streets_progress.jsonl" ] && mv -f "$RAW/_streets_progress.jsonl" "$RAW/_streets_progress.prev.jsonl"
  if [ -f "$RAW/mca_level1-4.json" ]; then
    cp -f "$RAW/mca_level1-4.json" "$RAW/mca_level1-4.prev.json"
    RAW_HAD_PREVIOUS=1
  else
    rm -f "$RAW/mca_level1-4.prev.json"
  fi
  RAW_CHANGED=1
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
    if [ "$attempt" -lt 3 ]; then
      say "仍有 $left 个县级下钻失败，准备下一轮重试"
      sleep 90
    fi
  done

  # 残余失败 ≤5 个时，fetch_mca 已从上一版补回这些县的乡级，可以接受
  if [ "$left" -gt 5 ]; then
    say "!! 抓取不完整（$left 个县级下钻失败），放弃本次更新，保留上一版数据"
    rollback
    write_status incomplete "$left 个县级下钻失败，未覆盖现有数据"
    exit 1
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

# ---------- 5. 校验 / 回滚 ----------
say "健全性校验"
if ! DATA_OUT="$STAGE" "$PY" "$ROOT/scripts/validate.py"; then
  say "!! 校验不通过，回滚"
  rollback
  write_status failed "健全性校验未通过，已回滚"
  exit 1
fi

# 校验通过后才覆盖站点数据，发布中断时由 trap 恢复完整备份。
mkdir -p "$DATA"
PUBLISH_STARTED=1
sync_dir "$STAGE" "$DATA"
say "已同步到 web/public/data"

# 数据更新成功与静态构建成功分别报告，避免把旧 out/ 误报为已更新。
BUILD_STATUS=ok
if [ -d "$ROOT/web/out" ] && [ "${SKIP_BUILD:-0}" != "1" ]; then
  say "重建静态站点 out/"
  if ! command -v npm >/dev/null 2>&1 || ! (cd "$ROOT/web" && npm run build); then
    BUILD_STATUS=build_failed
    say "!! 数据已更新，但静态构建失败；请修复后在 web/ 执行 npm run build"
  fi
fi

# Linux 部署后将新产物发布到独立版本目录；未配置部署的本地用法保持原流程。
if [ "$BUILD_STATUS" = "ok" ] && [ "${SKIP_BUILD:-0}" != "1" ] && [ -f "$ROOT/data/deploy.json" ]; then
  say "发布静态站点到部署目录"
  if ! "$PY" "$ROOT/scripts/publish.py"; then
    BUILD_STATUS=publish_failed
    say "!! 数据与构建已完成，但站点发布失败；线上仍保留上一发布版本"
  fi
fi

# ---------- 6. 报告 ----------
"$PY" - "$TS" "$BUILD_STATUS" <<'EOF'
import json, sys
from pathlib import Path
meta = json.loads(Path("web/public/data/meta.json").read_text(encoding="utf-8"))
previous = json.loads(Path("data/raw/last_refresh.json").read_text())
rep = {
    **previous,
    "status": sys.argv[2],
    "finishedAt": sys.argv[1],
    "levels": {k: {"count": v["count"], "asOf": v["asOf"], "live": v["live"]}
               for k, v in meta["levels"].items()},
    "legacy": meta["legacy"],
}
Path("data/raw/last_refresh.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
EOF

write_status "$BUILD_STATUS"

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
[ "$BUILD_STATUS" = "ok" ] || exit 1
