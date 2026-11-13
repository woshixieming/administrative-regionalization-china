"""从唯一一份展示数据生成规范记录；不导入搜索索引或 v/ 副本。"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

PARENTS = {2: "provinceCode", 3: "cityCode", 4: "areaCode", 5: "streetCode"}
VERSION = re.compile(r"[a-f0-9]{24}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records(source):
    source = Path(source)
    meta = read_json(source / "meta.json")
    version = meta.get("datasetVersion", "")
    if not VERSION.fullmatch(version):
        raise ValueError("数据必须包含有效的 datasetVersion；请先运行 build_dataset.py")
    ancestors, seen = {}, set()
    counts = Counter()
    for level in range(1, 6):
        if level == 1:
            groups = [read_json(source / "provinces.json")]
        else:
            def groups_at_level():
                for path in sorted((source / f"l{level}").glob("*.json")):
                    data = read_json(path)
                    yield data if level < 4 else [r for rows in data.values() for r in rows]
                if level == 2:
                    yield meta.get("directCities", [])
            groups = groups_at_level()
        for group in groups:
            for original in group:
                item = dict(original)
                code, name = item["code"], item["name"]
                if not isinstance(code, str) or not (re.fullmatch(r"[0-9]{12}", code) or level == 1 and code == "资料暂缺"):
                    raise ValueError("区划代码无效")
                if not isinstance(name, str) or not name.strip() or any(ord(char) < 32 for char in name):
                    raise ValueError("区划名称为空或包含控制字符")
                key = level, code
                if key in seen:
                    raise ValueError(f"重复记录 L{level} {code}")
                seen.add(key)
                parent = item.get(PARENTS.get(level)) if level > 1 else None
                if level > 1 and (level - 1, parent) not in ancestors:
                    raise ValueError(f"父层缺失 L{level} {code}")
                lineage, inherited = ancestors[(level - 1, parent)] if level > 1 else ([], False)
                lineage = [*lineage, code]
                historical = inherited or bool(item.get("legacy"))
                if level < 5:
                    ancestors[key] = lineage, historical
                item.update(level=level, parentCode=parent or "")
                counts[level] += not item.get("legacy") and not item.get("virtual")
                yield (version, level, code, name, level - 1 if parent else None, parent,
                       lineage[0], lineage, historical, item)
    for level in range(1, 6):
        if counts[level] != meta["levels"][str(level)]["count"]:
            raise ValueError(f"L{level} 记录数量与元数据不一致")


def documents(source):
    source = Path(source)
    yield "meta.json", read_json(source / "meta.json")
    yield "search/manifest.json", read_json(source / "search/manifest.json")
    yield "patches.json", read_json(source / "patches.json")
    for path in sorted((source / "changes").glob("*.json")):
        yield path.relative_to(source).as_posix(), read_json(path)
    status = source.parent / "status.json"
    yield "status.json", read_json(status) if status.exists() else {"label": "数据库数据已导入"}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def checksum(source):
    digest = hashlib.sha256()
    for row in records(source):
        digest.update((json_text(row) + "\n").encode())
    for path, body in documents(source):
        # 更新状态可随任务变化，不属于不可变数据版本。
        if path != "status.json":
            digest.update((path + "\n" + json_text(body) + "\n").encode())
    return digest.hexdigest()
