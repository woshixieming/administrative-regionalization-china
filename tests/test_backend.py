"""离线数据导入校验，以及显式指定隔离测试库后的 PostgreSQL 回归。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.config import ROOT, connection_options, load_config
from backend.dataset import records
from backend.manage import generate_sql, import_sql
from backend.repository import ApiError, Repository


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.write("meta.json", {"datasetVersion": "a" * 24, "generatedAt": "test", "directCities": [
            {"code": "410000000000", "name": "省直辖", "provinceCode": "410000000000", "virtual": True}],
            "levels": {str(i): {"count": 1 if i < 3 or i == 5 else 0} for i in range(1, 6)}})
        self.write("provinces.json", [{"code": "410000000000", "name": "河南省"}])
        self.write("l2/41.json", [{"code": "410100000000", "name": "郑州市", "provinceCode": "410000000000"}])
        self.write("l3/41.json", [{"code": "419002000000", "name": "历史县", "cityCode": "410000000000", "legacy": True}])
        self.write("l4/41.json", {"419002000000": [{"code": "419002001000", "name": "历史乡", "areaCode": "419002000000", "legacy": True}]})
        self.write("l5/41.json", {"419002001000": [{"code": "419002001001", "name": "测试社区", "streetCode": "419002001000"}]})
        self.write("search/manifest.json", {"total": 6})
        self.write("patches.json", {"records": []})

    def write(self, path, value):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_same_code_container_and_inherited_history(self):
        rows = list(records(self.source))
        self.assertEqual(len(rows), 6)
        village = rows[-1]
        self.assertTrue(village[8])
        self.assertEqual(village[7], ["410000000000", "410000000000", "419002000000", "419002001000", "419002001001"])

    def test_missing_parent_and_wrong_counts_reject_sql_before_writing(self):
        self.write("l3/41.json", [{"code": "419002000000", "name": "县", "cityCode": "999999999999"}])
        with self.assertRaises(ValueError):
            generate_sql(self.source, self.source / "bad.sql")
        self.assertFalse((self.source / "bad.sql").exists())

    def test_sql_escapes_quotes_and_does_not_import_version_copies(self):
        self.write("provinces.json", [{"code": "410000000000", "name": "省'名"}])
        self.write("v/ignored/provinces.json", [{"bad": "duplicate"}])
        output = self.source / "seed.sql"
        self.assertEqual(generate_sql(self.source, output), 6)
        text = output.read_text()
        self.assertIn("COPY import_regions", text)
        self.assertIn("SET CONSTRAINTS ALL IMMEDIATE", text)
        self.assertIn("COMMIT;", text)


@unittest.skipUnless(os.environ.get("DIVISIONS_TEST_CONFIG"), "未指定隔离 PostgreSQL 测试库")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.config = load_config(os.environ["DIVISIONS_TEST_CONFIG"])
        if not cls.config["postgres"]["dbname"].endswith("_test"):
            raise ValueError("只允许在名称以 _test 结尾的隔离库运行")
        cls.connect = staticmethod(lambda: psycopg.connect(**connection_options(cls.config)))

    def test_all_levels_match_metadata(self):
        with self.connect() as db:
            repo = Repository(db)
            counts = dict(db.execute("SELECT level,count(*) FROM divisions.regions WHERE version=%s AND NOT (payload @> '{\"legacy\":true}' OR payload @> '{\"virtual\":true}') GROUP BY level", (repo.version,)).fetchall())
            for level in range(1, 6):
                self.assertEqual(counts[level], repo.meta["levels"][str(level)]["count"])

    def test_switching_current_does_not_change_pinned_version(self):
        import psycopg
        from psycopg.types.json import Jsonb
        with self.connect() as db:
            original = Repository(db)
            version = "b" * 24
            with db.transaction():
                db.execute("INSERT INTO divisions.datasets(version,checksum,meta) VALUES(%s,%s,%s)",
                           (version, "test", Jsonb({**original.meta, "datasetVersion": version})))
                db.execute("UPDATE divisions.current_dataset SET version=%s", (version,))
                self.assertEqual(Repository(db).version, version)
                pinned = Repository(db, original.version)
                self.assertEqual(pinned.search("东城", "", 3, True)["datasetVersion"], original.version)
                self.assertGreater(len(pinned.data("provinces.json")), 0)
                raise psycopg.Rollback()
            self.assertEqual(Repository(db).version, original.version)

    def test_search_limit_paths_history_and_literal_wildcards(self):
        with self.connect() as db:
            repo = Repository(db)
            result = repo.search("社区", "", 5, True)
            self.assertGreater(result["total"], 200)
            self.assertEqual(len(result["hits"]), 200)
            self.assertTrue(all(len(hit["path"]) == 5 for hit in result["hits"]))
            self.assertEqual([(h["node"]["code"], h["node"]["level"]) for h in result["hits"]],
                             sorted((h["node"]["code"], h["node"]["level"]) for h in result["hits"]))
            visible = repo.search("社区", "", 5, False)
            self.assertLessEqual(visible["total"], result["total"])
            self.assertTrue(all(not n.get("legacy") for hit in visible["hits"] for n in hit["path"]))
            self.assertEqual(repo.search("%_", "", 0, True)["total"], 0)
            self.assertEqual(repo.search("' OR 1=1 --", "", 0, True)["total"], 0)

    def test_direct_container_and_subtree_export(self):
        with self.connect() as db:
            repo = Repository(db)
            upper = repo.data("search/upper/410000000000.json")
            self.assertTrue(any(row[2] == 1 and row[0] == "410000000000" for row in upper))
            self.assertTrue(any(row[2] == 2 and row[0] == "410000000000" for row in upper))
            rows = repo.subtree(3, "110101000000", True)["rows"]
            keys = {(r[2], r[0]) for r in rows}
            self.assertTrue(all((r[2] - 1, r[3]) in keys for r in rows if r[2] > 1))
            self.assertTrue(all(r[0] == "110101000000" for r in rows if r[2] == 3))
            self.assertTrue(any(r[2] == 5 for r in rows))
            with self.assertRaises(ApiError):
                Repository(db, "b" * 24)

    def test_incomplete_import_rolls_back(self):
        with self.connect() as db:
            original = Repository(db).version
        with tempfile.TemporaryDirectory() as task_dir:
            path = Path(task_dir) / "bad.sql"
            path.write_text("\\set ON_ERROR_STOP on\nBEGIN;\nCREATE TEMP TABLE import_regions(value text);\nCOPY import_regions FROM STDIN;\nmissing terminator\n")
            with self.assertRaises(RuntimeError):
                import_sql(self.config, path)
        with self.connect() as db:
            self.assertEqual(Repository(db).version, original)

    def test_http_api_uses_database_and_blocks_static_source(self):
        import threading
        import urllib.error
        import urllib.request
        from functools import partial
        from psycopg_pool import ConnectionPool
        from backend.server import Handler, Server
        static = ROOT / "web/out"
        with ConnectionPool(kwargs=connection_options(self.config), min_size=1, max_size=2) as pool:
            with Server(("127.0.0.1", 0), partial(Handler, directory=str(static)), pool, 4) as server:
                server.static_root = static.resolve()
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    for path in ("/api/health", "/api/data/meta.json", "/api/search?q=110101&level=3", "/api/export?level=3&code=110101000000"):
                        with urllib.request.urlopen(base + path, timeout=15) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIsInstance(json.load(response), dict)
                    for path, status in (("/data/meta.json", 404), ("/_next/../data/meta.json", 404),
                                         ("/api/search?q=x", 400), ("/api/search?q=aa&level=bad", 400),
                                         ("/api/search?q=aa&history=bad", 400), ("/api/search?q=aa&q=bb", 400)):
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(base + path, timeout=15)
                        self.assertEqual(error.exception.code, status)
                        self.assertIn("error", json.load(error.exception))
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def test_reimport_is_idempotent_and_conflicts_preserve_current(self):
        import_sql(self.config, ROOT / "database/data.sql")
        with self.connect() as db:
            repo = Repository(db)
            count = db.execute("SELECT count(*) FROM divisions.regions WHERE version=%s", (repo.version,)).fetchone()[0]
            self.assertEqual(count, repo.document("search/manifest.json")["total"])
            version = repo.version
        with tempfile.TemporaryDirectory() as task_dir:
            path = Path(task_dir) / "conflict.sql"
            # 用流式复制构造同版本不同校验和，禁止覆盖现有不可变版本。
            with (ROOT / "database/data.sql").open() as source, path.open("w") as out:
                for line in source:
                    if "AND checksum <> '" in line:
                        line = line.replace("AND checksum <> '", "AND checksum <> 'conflict-")
                    out.write(line)
            with self.assertRaises(RuntimeError):
                import_sql(self.config, path)
        with self.connect() as db:
            self.assertEqual(Repository(db).version, version)
