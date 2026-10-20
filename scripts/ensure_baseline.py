#!/usr/bin/env python3
"""确保村级冻结基线（统计局 2023 快照）存在于 data/cache/divisions-2023.csv。

村级无官方活源，该快照是不可变基线：一旦存在就永不重新下载，
仅在文件缺失时从 npm 包 @cndiv/source-2023 恢复（网络不可用时报错退出）。
"""
import shutil
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
TARGET = CACHE / "divisions-2023.csv"
SPEC = json.loads(Path(__file__).with_name("baseline.json").read_text())
PKG = SPEC["package"]
# 基线校验：统计局 2023 版五级全量行数（不含表头）
EXPECTED_ROWS = SPEC["rows"]
EXPECTED_SHA256 = SPEC["sha256"]


def validate_baseline(path):
    n = row_count(path)
    if n != EXPECTED_ROWS:
        raise RuntimeError(f"基线行数异常 {n}（期望 {EXPECTED_ROWS}）")
    with path.open("rb") as file:
        digest = hashlib.sha256()
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != EXPECTED_SHA256:
        raise RuntimeError("基线 SHA-256 不匹配，拒绝替换冻结快照")
    return n


def download() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="cndiv-"))
    try:
        subprocess.run(["npm", "pack", PKG], cwd=tmp, check=True, capture_output=True)
        tgz = next(tmp.glob("cndiv-source-2023-*.tgz"), None)
        if not tgz:
            raise RuntimeError("npm pack 未产出 tgz")
        with tarfile.open(tgz) as tf:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith("data/divisions.csv")),
                None,
            )
            if not member:
                raise RuntimeError("包内未找到 data/divisions.csv")
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError("无法读取包内 data/divisions.csv")
            # 校验候选文件后再替换，下载中断时保留已有基线。
            candidate = tmp / "divisions.csv"
            with candidate.open("wb") as out:
                shutil.copyfileobj(src, out)
        validate_baseline(candidate)
        # 临时目录可能在其他文件系统，先复制到目标目录再原子替换。
        with tempfile.NamedTemporaryFile(dir=CACHE, prefix="baseline-", delete=False) as f:
            pending = Path(f.name)
        try:
            shutil.copyfile(candidate, pending)
            pending.replace(TARGET)
        finally:
            pending.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return TARGET


def row_count(p: Path) -> int:
    n = 0
    with p.open("rb") as f:
        for _ in f:
            n += 1
    return n - 1  # 去掉表头


def main() -> int:
    if TARGET.exists():
        try:
            n = validate_baseline(TARGET)
            print(f"村级基线已存在：{TARGET}  {n} 行（跳过下载）")
            return 0
        except RuntimeError as exc:
            print(f"!! {exc}，尝试从固定版本恢复", file=sys.stderr)

    print(f"村级基线缺失，从 {PKG} 下载 ...")
    download()
    n = validate_baseline(TARGET)
    print(f"村级基线已落地：{TARGET}  {n} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
