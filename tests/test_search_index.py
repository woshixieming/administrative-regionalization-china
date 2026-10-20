import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SearchIndexTests(unittest.TestCase):
    def builder(self):
        from test_maintenance import load_script
        return load_script("build_dataset")

    def test_province_direct_county_gets_explicit_container_without_changing_source(self):
        province = {"code": "410000000000", "name": "河南省"}
        areas = [{"code": "419001000000", "name": "济源市", "cityCode": province["code"]}]
        streets = [{"code": "419001001000", "name": "街道", "areaCode": areas[0]["code"], "legacy": True}]
        villages = [{"code": "419001001001", "name": "社区", "streetCode": streets[0]["code"]}]
        meta = {"generatedAt": "test"}
        cities = []
        with tempfile.TemporaryDirectory() as task_dir:
            out = Path(task_dir)
            manifest = self.builder().write_search_index(out, [province], cities, areas, streets, villages, meta)
            rows = json.loads((out / "search/upper/410000000000.json").read_text()) + json.loads((out / "search/villages/4190.json").read_text())
            self.assertEqual(manifest["total"], 5)
            self.assertEqual(len({(r[2], r[0]) for r in rows}), 5)
            self.assertEqual(meta["directCities"][0]["code"], province["code"])
            self.assertTrue(meta["directCities"][0]["virtual"])
            self.assertEqual(cities, [])
            self.assertEqual(next(r for r in rows if r[2] == 4)[4], 1)

    def test_unrecognized_parent_rejects_index_generation(self):
        with tempfile.TemporaryDirectory() as task_dir:
            with self.assertRaises(ValueError):
                self.builder().write_search_index(Path(task_dir), [{"code": "410000000000", "name": "河南省"}], [],
                    [{"code": "419001000000", "name": "济源市", "cityCode": "missing"}], [], [], {"generatedAt": "test"})


if __name__ == "__main__":
    unittest.main()
