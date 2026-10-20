#!/usr/bin/env python3
"""Linux 一键部署：依赖检查、构建、版本发布、Nginx 和 cron 配置。"""
import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import schedule
from publish import (activate_release, check_managed_root, current_release, prepare_release,
                     health_check, project_lock, prune_releases, read_config, restore_release)

ROOT = Path(__file__).resolve().parent.parent
MARKER = "# Managed by china-divisions-explorer\n"


def run(command, *, capture=False, check=True, **kwargs):
    result = subprocess.run([str(arg) for arg in command], text=True, check=check,
                            capture_output=capture, **kwargs)
    return result


def admin(command, **kwargs):
    return run(([] if os.geteuid() == 0 else ["sudo"]) + list(command), **kwargs)


def domain_name(value):
    value = value.lower().rstrip(".")
    if len(value) > 253 or not value or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")):
        raise argparse.ArgumentTypeError("请提供有效域名（中文域名使用 Punycode）或 IPv4 地址")
    if re.fullmatch(r"[0-9.]+", value):
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("IPv4 地址无效") from exc
    return value


def port_number(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须位于 1–65535")
    return port


def nginx_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or re.search(r'[\x00-\x1f\x7f"\\$;{}#]', str(path)):
        raise argparse.ArgumentTypeError("路径必须为绝对路径，且不能包含 Nginx 特殊字符或控制字符")
    return path


def render_config(domain, port, destination):
    project_id = hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()
    template = (ROOT / "deploy/nginx.conf.template").read_text(encoding="utf-8")
    return template.replace("__PROJECT_ID__", project_id).replace("__DOMAIN__", domain).replace(
        "__PORT__", str(port)).replace("__WEB_ROOT__", str(destination / "current"))


def check_config_owner(path):
    if path.is_symlink():
        raise RuntimeError("Nginx 配置路径已是软链接，拒绝覆盖，请指定独立 .conf 文件")
    if path.exists():
        old = path.read_text(encoding="utf-8")
        project_id = hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()
        if not old.startswith(MARKER + f"# Project: {project_id}\n"):
            raise RuntimeError("目标 Nginx 配置不属于本项目，拒绝覆盖")
        return old
    return None


def install_os_dependencies(with_cron):
    missing = [name for name in ("nginx", "rsync", "curl") if not shutil.which(name)]
    need_cron = with_cron and not shutil.which("crontab")
    if not missing and not need_cron:
        return
    if shutil.which("apt-get"):
        admin(["apt-get", "update"])
        admin(["apt-get", "install", "-y", *missing] + (["cron"] if need_cron else []))
    else:
        manager = shutil.which("dnf") or shutil.which("yum")
        if not manager:
            raise RuntimeError("--install-deps 仅支持 apt-get / dnf / yum，请手动安装系统依赖")
        admin([manager, "install", "-y", *missing] + (["cronie"] if need_cron else []))


def preflight(args):
    if platform.system() != "Linux":
        raise RuntimeError("实际部署仅支持 Linux；本机可使用 --dry-run 预览")
    required = ["node", "npm", "rsync", "curl", "nginx", "systemctl"]
    if not args.no_cron:
        required.append("crontab")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"缺少依赖：{', '.join(missing)}；Node.js / Python 请预先准备，其余可使用 --install-deps")
    version = run(["node", "--version"], capture=True).stdout.strip()
    numbers = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    parts = tuple(int(n) for n in numbers.groups()) if numbers else (0, 0, 0)
    if not ((parts[0] == 20 and parts >= (20, 19, 0)) or
            (parts[0] == 22 and parts >= (22, 13, 0)) or parts[0] >= 24):
        raise RuntimeError("检查工具要求 Node.js 20.19+ / 22.13+ / 24+，建议使用 Node.js 22 LTS；脚本不会升级运行时")
    if not os.access(ROOT, os.W_OK) or not os.access(ROOT / "web", os.W_OK):
        raise RuntimeError("当前用户需要项目源码和 web/ 目录的写权限")
    if not args.no_cron:
        service = None
        for candidate in ("cron", "crond"):
            result = admin(["systemctl", "list-unit-files", candidate + ".service", "--no-legend"], capture=True)
            if any(line.startswith(candidate + ".service ") for line in result.stdout.splitlines()):
                service = candidate
                break
        if service is None:
            raise RuntimeError("未找到 cron/crond 服务，请安装并启用 cron，或使用 --no-cron")
        args.cron_service = service


def atomic_config(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as file:
        pending = Path(file.name)
        file.write(text)
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def restore_cron(previous):
    remaining, _ = schedule.split_crontab(schedule.read_crontab())
    restored = remaining + ("\n" if remaining and not remaining.endswith("\n") else "") + previous
    run(["crontab", "-"], input=restored, capture=True)


def deploy(args):
    with project_lock(ROOT):
        destination = check_managed_root(ROOT, args.publish_root)
        old_nginx = check_config_owner(args.nginx_conf)
        runtime = ROOT / "data/deploy.json"
        old_runtime = runtime.read_text() if runtime.exists() else None
        old_config = None
        if old_runtime is not None:
            old_config = read_config(ROOT)
        rendered = render_config(args.domain, args.port, destination)
        old_expected_hash = None
        if old_nginx is not None:
            old_expected_hash = (old_config or {}).get("nginxConfigSha256")
            if not old_expected_hash:
                expected = render_config(old_config["domain"], old_config["port"], Path(old_config["publishRoot"])) if old_config else rendered
                old_expected_hash = hashlib.sha256(expected.encode()).hexdigest()
            if not args.keep_nginx and hashlib.sha256(old_nginx.encode()).hexdigest() != old_expected_hash:
                raise RuntimeError("Nginx 配置已被手工修改，拒绝覆盖；升级时可使用 --keep-nginx 保留 HTTPS 等设置")
        if args.keep_nginx:
            if old_nginx is None or old_config is None:
                raise RuntimeError("--keep-nginx 需要已有的本项目部署")
            if (old_config["domain"], old_config["port"], old_config["publishRoot"]) != (args.domain, args.port, str(destination)):
                raise RuntimeError("--keep-nginx 时域名、端口和发布目录须与已有部署一致")
        if os.geteuid() != 0:
            run(["sudo", "-v"])
        if args.install_deps:
            install_os_dependencies(not args.no_cron)
        preflight(args)
        admin(["nginx", "-t"], capture=True)
        config = {"version": 1, "projectRoot": str(ROOT.resolve()), "publishRoot": str(destination),
                  "domain": args.domain, "port": args.port, "nginxConf": str(args.nginx_conf),
                  "nginxConfigSha256": old_expected_hash if args.keep_nginx else hashlib.sha256(rendered.encode()).hexdigest()}
        if not (ROOT / "web/public/data/search/manifest.json").is_file():
            if not (ROOT / "data/raw/mca_level1-4.json").is_file():
                raise RuntimeError("缺少数据，请上传完整 web/public/data/，或 data/raw/mca_level1-4.json 与冻结基线")
            run([sys.executable, ROOT / "scripts/ensure_baseline.py"], cwd=ROOT)
            run([sys.executable, ROOT / "scripts/build_dataset.py"], cwd=ROOT)
        run([sys.executable, ROOT / "scripts/validate.py"], cwd=ROOT)
        print("安装前端依赖并构建静态站点…", flush=True)
        run(["npm", "ci", "--include=dev"], cwd=ROOT / "web")
        run(["npm", "run", "build"], cwd=ROOT / "web")
        if not destination.exists():
            admin(["install", "-d", "-m", "0755", "-o", str(os.getuid()), "-g", str(os.getgid()), destination])
        previous = current_release(ROOT, destination)
        release = prepare_release(ROOT, destination)
        old_cron = schedule.split_crontab(schedule.read_crontab())[1] if not args.no_cron else ""
        config_touched = switched = cron_touched = runtime_touched = False
        try:
            if not args.keep_nginx:
                with tempfile.TemporaryDirectory(prefix="divisions-nginx-") as temporary:
                    candidate = Path(temporary) / "site.conf"
                    candidate.write_text(rendered)
                    if not args.nginx_conf.parent.exists():
                        admin(["install", "-d", "-m", "0755", args.nginx_conf.parent])
                    config_touched = True
                    admin(["install", "-m", "0644", candidate, args.nginx_conf])
            admin(["nginx", "-t"], capture=True)
            dump = admin(["nginx", "-T"], capture=True)
            # -T 的原始内容可能包含其他站点配置，绝不打印。
            if f"# configuration file {args.nginx_conf}:" not in dump.stdout:
                raise RuntimeError("Nginx 未加载目标 .conf，请将其放入 http 块已包含的目录")
            if any("conflicting server name" in line and args.domain in line for line in dump.stderr.splitlines()):
                raise RuntimeError("域名与已有 Nginx 站点冲突，拒绝替换其他站点")
            activate_release(ROOT, destination, release)
            switched = True
            admin(["systemctl", "enable", "--now", "nginx"])
            admin(["systemctl", "reload", "nginx"])
            health_check(config)
            runtime_touched = True
            atomic_config(runtime, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            if not args.no_cron:
                admin(["systemctl", "enable", "--now", args.cron_service])
                cron_touched = True
                run([sys.executable, ROOT / "scripts/schedule.py", "install"], cwd=ROOT)
        except BaseException:
            print("部署未完成，恢复上次站点和本项目配置…", file=sys.stderr)
            errors = []
            actions = []
            if switched:
                actions.append(lambda: restore_release(ROOT, destination, previous))
            if runtime_touched:
                actions.append(lambda: atomic_config(runtime, old_runtime) if old_runtime is not None else runtime.unlink(missing_ok=True))
            if config_touched:
                def restore_nginx():
                    if old_nginx is None:
                        admin(["rm", "-f", args.nginx_conf])
                    else:
                        with tempfile.TemporaryDirectory() as folder:
                            backup = Path(folder) / "site.conf"
                            backup.write_text(old_nginx)
                            admin(["install", "-m", "0644", backup, args.nginx_conf])
                    admin(["nginx", "-t"], capture=True)
                    # 首次部署时 nginx 可能尚未启动；恢复配置本身仍需完成。
                    admin(["systemctl", "reload", "nginx"], check=False, capture=True)
                actions.append(restore_nginx)
            if cron_touched:
                actions.append(lambda: restore_cron(old_cron))
            for action in actions:
                try:
                    action()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if errors:
                print("部分恢复操作失败，请检查站点配置和定时任务：" + ", ".join(errors), file=sys.stderr)
            raise
        try:
            prune_releases(ROOT, destination)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"部署已成功，旧版本清理未完成：{type(exc).__name__}", file=sys.stderr)
    port = "" if args.port == 80 else f":{args.port}"
    print(f"部署成功：http://{args.domain}{port}/")
    print(f"当前版本：{release.name}")
    print("定时更新：保持原有配置" if args.no_cron else "定时更新：每月 15 日 03:00（服务器系统时区）")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=domain_name, help="域名或 IPv4 地址，部署时必填")
    parser.add_argument("--port", type=port_number, default=80)
    parser.add_argument("--publish-root", type=nginx_path, default=Path("/var/www/china-divisions"))
    parser.add_argument("--nginx-conf", type=nginx_path, default=Path("/etc/nginx/conf.d/china-divisions.conf"))
    parser.add_argument("--install-deps", action="store_true", help="通过 apt/dnf/yum 安装 Nginx、rsync、curl、cron；不安装或升级 Node/Python")
    parser.add_argument("--no-cron", action="store_true", help="不安装或修改本项目定时任务，已有任务保留")
    parser.add_argument("--keep-nginx", action="store_true", help="升级时保留本项目手工修改过的 Nginx / HTTPS 配置")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不构建或修改任何配置")
    parser.add_argument("--status", action="store_true", help="查看当前部署并检查 HTTP 页面和数据")
    args = parser.parse_args(argv)
    if args.status:
        config = read_config(ROOT)
        release = current_release(ROOT, Path(config["publishRoot"]))
        if release is None:
            raise RuntimeError("尚无已发布版本")
        health_check(config)
        print(f"站点检查通过：http://{config['domain']}:{config['port']}/\n当前版本：{release.name}")
        return 0
    if not args.domain:
        parser.error("部署或预览需要 --domain 域名或IPv4地址")
    if args.nginx_conf.suffix != ".conf":
        parser.error("--nginx-conf 必须是 .conf 文件")
    if args.dry_run:
        print("部署预览（没有执行任何系统操作）")
        print(f"项目：{ROOT}\n发布目录：{args.publish_root}\nNginx 配置：{args.nginx_conf}")
        print("流程：检查 Linux / 运行时 → 校验数据 → npm ci → 构建 → 新版本 → nginx -t → 原子切换 → HTTP 检查 → cron")
        print(render_config(args.domain, args.port, args.publish_root))
        return 0
    if platform.system() != "Linux":
        raise RuntimeError("实际部署仅支持 Linux；本机可使用 --dry-run 预览")
    deploy(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        sys.exit(1)
