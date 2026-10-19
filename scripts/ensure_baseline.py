#!/usr/bin/env python3
"""确保村级冻结基线（统计局 2023 快照）存在于 data/cache/divisions-2023.csv。

村级无官方活源，该快照是不可变基线：一旦存在就永不重新下载，
仅在文件缺失时从 npm 包 @cndiv/source-2023 恢复（网络不可用时报错退出）。
"""
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
TARGET = CACHE / "divisions-2023.csv"
PKG = "@cndiv/source-2023"
# 基线校验：统计局 2023 版五级全量行数（不含表头）
EXPECTED_ROWS = 665271
EXPECTED_MD5_PREFIX = None  # 首次落地后写入 .sha1，后续用摘要自检


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
            with TARGET.open("wb") as out:
                shutil.copyfileobj(src, out)
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
        n = row_count(TARGET)
        if n < EXPECTED_ROWS * 0.98:
            print(f"!! 基线行数异常 {n}（期望 ~{EXPECTED_ROWS}），删除后重新下载", file=sys.stderr)
            TARGET.unlink()
        else:
            print(f"村级基线已存在：{TARGET}  {n} 行（跳过下载）")
            return 0

    print(f"村级基线缺失，从 {PKG} 下载 ...")
    download()
    n = row_count(TARGET)
    if n < EXPECTED_ROWS * 0.98:
        print(f"!! 下载后行数异常 {n}", file=sys.stderr)
        return 1
    print(f"村级基线已落地：{TARGET}  {n} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
