"""生成可公开的更新状态；内部原因和路径仅保留在私有报告中。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = {"running": "更新进行中", "ok": "更新成功", "skipped": "接口暂不可用，保留原版",
          "failed": "更新失败，保留可用版本", "incomplete": "抓取不完整，未发布",
          "build_failed": "静态构建失败，保留已发布版本", "publish_failed": "发布失败，已回切原版"}


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    pending.chmod(0o644)
    pending.replace(path)


def write_report(project, status, reason=""):
    if status not in LABELS:
        raise ValueError("未知更新状态")
    private_path = project / "data/raw/last_refresh.json"
    old = read(private_path)
    now = datetime.now(timezone.utc).isoformat()
    private = {**old, "status": status, "checkedAt": now}
    if reason:
        private["reason"] = reason
    if status == "running":
        private["startedAt"] = now
        private.pop("reason", None)
        private.pop("finishedAt", None)
    else:
        private["finishedAt"] = now
    if status == "ok":
        private["lastSuccessAt"] = now
    atomic(private_path, private)
    roots = [project / "web/public", project / "web/out"]
    active = read(project / "web/out/data/meta.json") or read(project / "web/public/data/meta.json")
    if (project / "data/deploy.json").is_file():
        from publish import read_config, current_release
        config = read_config(project)
        release = current_release(project, Path(config["publishRoot"]))
        if release:
            roots.append(release)
            active = read(release / "data/meta.json")
    public = {key: private.get(key) for key in ("status", "startedAt", "finishedAt", "lastSuccessAt", "checkedAt")}
    public.update({"label": LABELS[status], "datasetVersion": active.get("datasetVersion"),
                   "dataGeneratedAt": active.get("generatedAt"),
                   "counts": {key: value["count"] for key, value in active.get("levels", {}).items()}})
    for root in roots:
        if root.is_dir():
            atomic(root / "status.json", public)
    return public


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("status", choices=LABELS)
    parser.add_argument("reason", nargs="?", default="")
    args = parser.parse_args()
    write_report(ROOT, args.status, args.reason)
