#!/usr/bin/env python3
"""发布完整静态产物并原子切换 current；由部署脚本和定时刷新共用。"""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER_FILE = ".china-divisions.json"
RELEASE_FILE = ".release.json"


@contextlib.contextmanager
def project_lock(project):
    (project / "data").mkdir(exist_ok=True)
    with (project / "data/.refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有部署或数据刷新任务运行，请稍后重试") from exc
        yield


def read_config(project):
    config = json.loads((project / "data/deploy.json").read_text(encoding="utf-8"))
    if config.get("version") != 1 or config.get("projectRoot") != str(project.resolve()):
        raise RuntimeError("部署配置不属于当前项目路径，请重新运行 deploy.sh")
    return config


def check_managed_root(project, destination):
    if not destination.is_absolute() or destination.is_symlink():
        raise RuntimeError("发布目录必须为绝对路径，且不能是软链接")
    destination = destination.resolve()
    if destination == Path("/") or destination == project.resolve() or destination.is_relative_to(project.resolve()):
        raise RuntimeError("发布目录必须独立于项目源码目录")
    marker = destination / OWNER_FILE
    if marker.exists():
        if json.loads(marker.read_text())["projectRoot"] != str(project.resolve()):
            raise RuntimeError("发布目录已被其他项目或旧路径占用")
    elif destination.exists() and any(destination.iterdir()):
        raise RuntimeError("发布目录非空且没有本项目标记，拒绝覆盖")
    releases = destination / "releases"
    if releases.is_symlink():
        raise RuntimeError("releases 不能是软链接")
    return destination


def validate_output(source):
    for name in ("index.html", "404.html", "data/meta.json", "data/provinces.json", "data/search/manifest.json"):
        if not (source / name).is_file():
            raise RuntimeError(f"静态产物缺少 {name}，请先完成构建")
    meta = json.loads((source / "data/meta.json").read_text())
    manifest = json.loads((source / "data/search/manifest.json").read_text())
    if not meta.get("generatedAt") or meta["generatedAt"] != manifest.get("generatedAt"):
        raise RuntimeError("静态产物的数据版本与搜索索引不一致")
    if meta.get("datasetVersion") != manifest.get("datasetVersion"):
        raise RuntimeError("静态产物的数据版本号与搜索索引不一致")
    if not (source / "_next/static").is_dir():
        raise RuntimeError("静态产物缺少前端资源目录")
    if meta.get("datasetVersion"):
        version = meta["datasetVersion"]
        if len(version) != 24 or any(c not in "0123456789abcdef" for c in version):
            raise RuntimeError("数据版本号无效")
        frozen = source / "data/v" / version / "meta.json"
        if not frozen.is_file() or json.loads(frozen.read_text()).get("datasetVersion") != version:
            raise RuntimeError("静态产物缺少对应的固定版本数据")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise RuntimeError("静态产物包含软链接，请重新构建后发布")
    return meta


def remove_tree(path):
    # 只用于调用方确认属于本项目的版本目录；目录清理遵循现有 rsync 方式。
    with tempfile.TemporaryDirectory(prefix="divisions-empty-") as empty:
        subprocess.run(["rsync", "-a", "--delete", empty + "/", str(path) + "/"], check=True)
    path.rmdir()


def retained_releases(project, destination, limit=2):
    current = current_release(project, destination)
    others = []
    for path in (destination / "releases").glob("release-*"):
        marker = path / RELEASE_FILE
        if path.is_symlink() or not marker.is_file() or path == current:
            continue
        info = json.loads(marker.read_text())
        if info.get("projectRoot") == str(project.resolve()):
            others.append((info["createdAt"], path))
    return ([current] if current else []) + [p for _, p in sorted(others, reverse=True)[:max(0, limit - bool(current))]]


def copy_immutable(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def preserve_previous_resources(project, destination, pending):
    for release in retained_releases(project, destination):
        info = json.loads((release / RELEASE_FILE).read_text())
        assets = info.get("assets")
        if assets is None:
            assets = [str(p.relative_to(release / "_next/static")) for p in (release / "_next/static").rglob("*") if p.is_file()]
        for name in assets:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("旧版本资源清单包含无效路径")
            source = release / "_next/static" / relative
            if source.is_file() and not source.is_symlink():
                copy_immutable(source, pending / "_next/static" / relative)
        version = info.get("datasetVersion")
        if version and len(version) == 24 and all(c in "0123456789abcdef" for c in version):
            source = release / "data/v" / version
            target = pending / "data/v" / version
            if source.is_dir() and not target.exists():
                shutil.copytree(source, target, copy_function=copy_immutable)


def prepare_release(project, destination):
    destination = check_managed_root(project, destination)
    source = project / "web/out"
    meta = validate_output(source)
    expected = json.loads((project / "web/public/data/meta.json").read_text())
    if meta != expected:
        raise RuntimeError("构建产物不是当前数据版本，请重新构建后发布")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / OWNER_FILE).write_text(json.dumps({"projectRoot": str(project.resolve())}))
    releases = destination / "releases"
    releases.mkdir(exist_ok=True)
    suffix = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    pending = releases / (".building-" + suffix)
    final = releases / ("release-" + suffix)
    try:
        shutil.copytree(source, pending)
        versions = pending / "data/v"
        if versions.is_dir():
            for old in versions.iterdir():
                if old.name != meta.get("datasetVersion"):
                    remove_tree(old)
        assets = [str(p.relative_to(pending / "_next/static")) for p in (pending / "_next/static").rglob("*") if p.is_file()]
        preserve_previous_resources(project, destination, pending)
        (pending / RELEASE_FILE).write_text(json.dumps({"projectRoot": str(project.resolve()),
            "createdAt": time.time_ns(), "generatedAt": meta["generatedAt"],
            "datasetVersion": meta.get("datasetVersion"), "assets": assets}))
        for folder, _, files in os.walk(pending):
            Path(folder).chmod(0o755)
            for name in files:
                (Path(folder) / name).chmod(0o644)
        pending.rename(final)
    finally:
        if pending.exists():
            remove_tree(pending)
    return final


def current_release(project, destination):
    check_managed_root(project, destination)
    current = destination / "current"
    if not current.is_symlink():
        if current.exists():
            raise RuntimeError("current 已存在且不是软链接，拒绝覆盖")
        return None
    release = current.resolve()
    if release.parent != (destination / "releases").resolve() or release.is_symlink():
        raise RuntimeError("current 指向非本项目版本，拒绝覆盖")
    marker = json.loads((release / RELEASE_FILE).read_text())
    if marker["projectRoot"] != str(project.resolve()):
        raise RuntimeError("current 指向其他项目版本")
    return release


def activate_release(project, destination, release):
    current_release(project, destination)
    if release.parent.resolve() != (destination / "releases").resolve() or release.is_symlink():
        raise RuntimeError("候选版本必须位于本项目 releases 目录")
    marker = json.loads((release / RELEASE_FILE).read_text())
    if marker["projectRoot"] != str(project.resolve()):
        raise RuntimeError("候选版本不属于当前项目")
    temporary = destination / (".current-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(Path("releases") / release.name)
        os.replace(temporary, destination / "current")
    finally:
        temporary.unlink(missing_ok=True)


def restore_release(project, destination, previous):
    if previous is not None:
        activate_release(project, destination, previous)
    elif current_release(project, destination) is not None:
        (destination / "current").unlink()


def prune_releases(project, destination, keep=3):
    current = current_release(project, destination)
    owned = []
    for path in (destination / "releases").glob("release-*"):
        if path.is_symlink() or not path.is_dir():
            continue
        marker = path / RELEASE_FILE
        if not marker.is_file():
            continue
        info = json.loads(marker.read_text())
        if info.get("projectRoot") == str(project.resolve()):
            owned.append((info["createdAt"], path))
    owned.sort(reverse=True)
    retained = {p for _, p in owned[:keep]} | {current}
    for _, path in owned:
        if path not in retained:
            remove_tree(path)


def health_check(config):
    root = Path(config["publishRoot"]) / "current"
    expected = json.loads((root / "data/meta.json").read_text())
    base = f"http://127.0.0.1:{config['port']}"
    endpoints = ["/", "/data/meta.json", "/data/search/manifest.json"]
    if expected.get("datasetVersion"):
        endpoints.append(f"/data/v/{expected['datasetVersion']}/meta.json")
    for attempt in range(3):
        try:
            for endpoint in endpoints:
                request = urllib.request.Request(base + endpoint, headers={"Host": config["domain"]})
                # 只访问本机 Nginx，不经过用户配置的 HTTP 代理。
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                    data = response.read()
                    if response.status != 200:
                        raise RuntimeError(f"健康检查失败：{endpoint}")
                if endpoint == "/":
                    if data != (root / "index.html").read_bytes():
                        raise RuntimeError("Nginx 返回的不是本次发布页面，请检查域名冲突或配置加载位置")
                elif json.loads(data).get("generatedAt") != expected["generatedAt"]:
                    raise RuntimeError("线上数据与本次发布版本不一致")
            return
        except (OSError, ValueError, RuntimeError):
            if attempt == 2:
                raise
            time.sleep(0.3)



def publish(project, config):
    destination = Path(config["publishRoot"])
    previous = current_release(project, destination)
    release = prepare_release(project, destination)
    activate_release(project, destination, release)
    try:
        health_check(config)
    except BaseException:
        restore_release(project, destination, previous)
        raise
    try:
        prune_releases(project, destination)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"版本已发布，旧版本清理未完成：{type(exc).__name__}", file=sys.stderr)
    print(f"已发布静态站点：{release.name}")
    return release


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    config = read_config(ROOT)
    if os.environ.get("DIVISIONS_REFRESH_LOCKED") == "1":
        publish(ROOT, config)
    else:
        with project_lock(ROOT):
            publish(ROOT, config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR  发布失败：{exc}", file=sys.stderr)
        sys.exit(1)
