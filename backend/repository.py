"""数据库读取层；所有外部参数使用绑定变量。"""
import re

from .dataset import PARENTS, VERSION


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def require_code(value, province=False):
    if not re.fullmatch(r"[0-9]{12}", value) and not (province and value == "资料暂缺"):
        raise ApiError(400, "区划代码无效")
    return value


def node(payload):
    return {**payload, "legacy": bool(payload.get("legacy")), "virtual": bool(payload.get("virtual"))}


def index_row(payload):
    extra = {key: payload[key] for key in ("carriedOver", "carriedAsOf", "patchId", "patchSource", "effectiveDate") if payload.get(key)}
    row = [payload["code"], payload["name"], payload["level"], payload["parentCode"],
           int(bool(payload.get("legacy"))), int(bool(payload.get("virtual")))]
    return row + [extra] if extra else row


class Repository:
    def __init__(self, connection, version=None):
        self.db = connection
        if version is not None and not VERSION.fullmatch(version):
            raise ApiError(400, "数据版本号无效")
        row = self.db.execute(
            "SELECT version,meta FROM divisions.datasets WHERE version = %s" if version else
            "SELECT d.version,d.meta FROM divisions.datasets d JOIN divisions.current_dataset c ON c.version=d.version",
            (version,) if version else (),
        ).fetchone()
        if not row:
            raise ApiError(404 if version else 503, "数据版本不存在或尚未导入，请导入数据或刷新页面")
        self.version, self.meta = row

    def document(self, path):
        row = self.db.execute("SELECT body FROM divisions.documents WHERE version=%s AND path=%s", (self.version, path)).fetchone()
        if not row:
            raise ApiError(404, "版本报告不存在")
        return row[0]

    def rows(self, where, params=()):
        return self.db.execute(
            "SELECT payload FROM divisions.regions WHERE version=%s AND " + where + " ORDER BY code COLLATE \"C\",level",
            (self.version, *params),
        ).fetchall()

    def data(self, path):
        if path == "meta.json":
            return self.meta
        if path == "provinces.json":
            return [row[0] for row in self.rows("level=1")]
        match = re.fullmatch(r"l([23])/([0-9]{12}|资料暂缺)\.json", path)
        if match:
            level, parent = int(match[1]), require_code(match[2], province=match[1] == "2")
            rows = self.rows("level=%s AND parent_code=%s", (level, parent))
            # 省直辖容器由现有前端从 meta.directCities 补入，不能重复返回。
            direct = {item["code"] for item in self.meta.get("directCities", [])} if level == 2 else set()
            return [r[0] for r in rows if r[0]["code"] not in direct]
        match = re.fullmatch(r"l([45])/([0-9]{4})\.json", path)
        if match:
            level, shard = int(match[1]), match[2]
            rows = self.rows("level=%s AND code LIKE %s", (level, shard + "%"))
            groups = {}
            for (item,) in rows:
                groups.setdefault(item[PARENTS[level]], []).append(item)
            return groups
        match = re.fullmatch(r"search/upper/([0-9]{12}|资料暂缺)\.json", path)
        if match:
            return [index_row(r[0]) for r in self.rows("province_code=%s AND level<5", (require_code(match[1], True),))]
        match = re.fullmatch(r"search/villages/([0-9]{4})\.json", path)
        if match:
            return [index_row(r[0]) for r in self.rows("level=5 AND code LIKE %s", (match[1] + "%",))]
        if path in ("search/manifest.json", "patches.json", "changes/summary.json") or re.fullmatch(
            r"changes/(added|removed|renamed|reparented|statusChanged)-[1-5]\.json", path
        ):
            return self.document(path)
        raise ApiError(404, "接口不存在")

    def paths(self, matches):
        keys = {(level, code) for item in matches for level, code in enumerate(item[1], 1)}
        if not keys:
            return []
        levels, codes = zip(*sorted(keys))
        ancestors = self.db.execute("""
            SELECT r.level,r.code,r.payload FROM divisions.regions r
            JOIN unnest(%s::smallint[],%s::text[]) k(level,code) ON r.level=k.level AND r.code=k.code
            WHERE r.version=%s
        """, (list(levels), list(codes), self.version)).fetchall()
        lookup = {(level, code): node(payload) for level, code, payload in ancestors}
        return [{"node": node(item[0]), "path": [lookup[(level, code)] for level, code in enumerate(item[1], 1)]} for item in matches]

    def search(self, query, province, level, history):
        if not 2 <= len(query) <= 100:
            raise ApiError(400, "请输入 2 至 100 个字或数字")
        if level not in range(6):
            raise ApiError(400, "搜索层级无效")
        where, params = ["version=%s"], [self.version]
        # LIKE 中的通配符必须按字面量搜索，不能扩大匹配范围。
        if re.fullmatch(r"[0-9]+", query):
            where.append("code LIKE %s")
            params.append(query + "%")
        else:
            escaped = query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
            where.append("lower(name) LIKE %s ESCAPE '!'")
            params.append("%" + escaped + "%")
        if province:
            where.append("province_code=%s")
            params.append(require_code(province, True))
        if level:
            where.append("level=%s")
            params.append(level)
        if not history:
            where.append("NOT legacy_path")
        matches = self.db.execute(
            "SELECT payload,lineage,count(*) OVER() FROM divisions.regions WHERE " + " AND ".join(where) +
            ' ORDER BY code COLLATE "C",level LIMIT 200', params,
        ).fetchall()
        return {"hits": self.paths(matches), "total": matches[0][2] if matches else 0,
                "datasetVersion": self.version, "generatedAt": self.meta["generatedAt"]}

    def subtree(self, level, code, history):
        if level not in range(1, 6):
            raise ApiError(400, "导出层级无效")
        require_code(code, level == 1)
        anchor = self.db.execute("SELECT lineage FROM divisions.regions WHERE version=%s AND level=%s AND code=%s",
                                 (self.version, level, code)).fetchone()
        if not anchor:
            raise ApiError(404, "所选区域不存在")
        # GIN 先按代码筛选，再按层级校验，避免同码虚拟容器混入兄弟节点。
        rows = self.rows("((lineage @> ARRAY[%s]::text[] AND lineage[%s]=%s) OR (level<%s AND code=ANY(%s::text[])))" +
                         (" AND NOT legacy_path" if not history else ""),
                         (code, level, code, level, anchor[0]))
        return {"datasetVersion": self.version, "rows": [index_row(r[0]) for r in rows]}
