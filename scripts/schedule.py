#!/usr/bin/env python3
"""管理当前用户的行政区划刷新 cron 任务（macOS / Linux，无需桌面应用）。"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN = "# BEGIN china-divisions-explorer refresh"
END = "# END china-divisions-explorer refresh"
DEFAULT_SCHEDULE = "0 3 15 * *"


def read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and "no crontab for" in result.stderr.lower():
        return ""
    raise RuntimeError(f"读取 crontab 失败：{result.stderr.strip()}")


def split_crontab(content: str) -> tuple[str, str]:
    """仅移除本项目的标记块，保留其他任务；标记损坏时拒绝覆盖。"""
    if BEGIN not in content and END not in content:
        return content, ""
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError("项目 cron 标记不完整或重复，请先人工检查 crontab -l")
    start, end = starts[0], ends[0] + 1
    return "".join(lines[:start] + lines[end:]), "".join(lines[start:end])


def validate_schedule(value: str) -> str:
    fields = value.split()
    if len(fields) != 5:
        raise argparse.ArgumentTypeError("cron 表达式必须为五段，例如 '0 3 15 * *'")
    for field, (lo, hi) in zip(fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]):
        for part in field.split(","):
            if not re.fullmatch(r"(?:\*|\d+(?:-\d+)?)(?:/[1-9]\d*)?", part):
                raise argparse.ArgumentTypeError("cron 表达式仅支持数字、*、范围、列表和正整数步长")
            base = part.split("/")[0]
            if base != "*":
                bounds = [int(n) for n in base.split("-")]
                if any(n < lo or n > hi for n in bounds) or bounds[0] > bounds[-1]:
                    raise argparse.ArgumentTypeError(f"cron 字段 {field} 超出范围 {lo}-{hi}")
    return " ".join(fields)


def make_block(schedule: str) -> str:
    bash = shutil.which("bash")
    if not bash:
        raise RuntimeError("未找到 bash")
    # 安装时记录本机环境；迁移后重新安装即可，无需更改源码中的路径。
    required = {name: shutil.which(name) for name in ("curl", "rsync", "node", "npm")}
    missing = [name for name, executable in required.items() if not executable]
    if missing:
        raise RuntimeError(f"缺少定时刷新依赖：{', '.join(missing)}")
    # 只记录必要工具目录，排除桌面应用注入的临时 PATH。
    executables = [sys.executable, bash, *required.values()]
    tool_dirs = [str(Path(executable).parent) for executable in executables]
    runtime_path = os.pathsep.join(dict.fromkeys(tool_dirs + os.defpath.split(os.pathsep)))
    values = [runtime_path, sys.executable,
              bash, str(ROOT / "scripts/refresh.sh"), str(ROOT / "data/logs/scheduled.log")]
    if any("\n" in value or "\r" in value for value in values):
        raise RuntimeError("路径或 PATH 不能包含换行符")
    path, python, shell, script, log = map(shlex.quote, values)
    command = f"PATH={path} PY={python} {shell} {script} >> {log} 2>&1"
    # cron 在 shell 解析前处理百分号，包括引号内的百分号。
    command = command.replace("%", "\\%")
    return f"{BEGIN}\n{schedule} {command}\n{END}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    install = subparsers.add_parser("install", help="安装或更新定时任务，保留其他 cron 任务")
    install.add_argument("--schedule", type=validate_schedule, default=DEFAULT_SCHEDULE,
                         help="默认每月 15 日 03:00，使用系统时区")
    install.add_argument("--dry-run", action="store_true", help="仅打印将安装的项目任务")
    subparsers.add_parser("status", help="查看本项目定时任务")
    subparsers.add_parser("remove", help="仅卸载本项目任务")
    args = parser.parse_args()
    if args.action == "install" and args.dry_run:
        print(make_block(args.schedule), end="")
        return 0
    if not shutil.which("crontab"):
        raise RuntimeError("未找到 crontab，请先安装并启用系统 cron 服务")
    remaining, current = split_crontab(read_crontab())
    if args.action == "status":
        print(current or "本项目尚未安装 cron 任务", end="" if current else "\n")
        return 0
    if args.action == "remove" and not current:
        print("本项目尚未安装 cron 任务")
        return 0
    block = make_block(args.schedule) if args.action == "install" else ""
    if args.action == "install":
        (ROOT / "data/logs").mkdir(parents=True, exist_ok=True)
    updated = remaining + ("\n" if remaining and not remaining.endswith("\n") else "") + block
    result = subprocess.run(["crontab", "-"], input=updated, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"写入 crontab 失败：{result.stderr.strip()}")
    _, installed = split_crontab(read_crontab())
    if installed != block:
        raise RuntimeError("crontab 回读结果不一致，请检查 crontab -l")
    print("定时任务已安装（使用系统时区）" if block else "本项目定时任务已卸载")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        sys.exit(1)
