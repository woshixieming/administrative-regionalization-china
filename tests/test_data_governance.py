import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from test_maintenance import ROOT, TemporaryProject, load_script


class DataGovernanceTests(TemporaryProject):
    def test_year_change_does_not_advance_unconfirmed_cutoff(self):
        module = load_script("provenance")
        class January(date):
            @classmethod
            def today(cls):
                return cls(2027, 1, 2)
        with patch.object(module, "date", January), patch.dict(os.environ, {"MCA_AS_OF": ""}):
            self.assertEqual(module.official_cutoff({"asOf": "2025-12-31"}), ("2025-12-31", "retained"))
            with self.assertRaises(ValueError):
                module.official_cutoff({})

    def test_same_line_count_cannot_hide_modified_baseline(self):
        module = load_script("ensure_baseline")
        original = b"code,name\n1,first\n2,second\n"
        target = self.write("baseline.csv", original.decode())
        with patch.object(module, "EXPECTED_ROWS", 2), patch.object(module, "EXPECTED_SHA256", hashlib.sha256(original).hexdigest()):
            self.assertEqual(module.validate_baseline(target), 2)
            target.write_text("code,name\n1,modified\n2,second\n")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                module.validate_baseline(target)

    def test_fixed_version_remains_unchanged_after_rebuilding_linked_files(self):
        module = load_script("build_dataset")
        out = self.root / "data"
        out.mkdir()
        module.dump_json(out / "meta.json", {"datasetVersion": "a" * 24})
        module.dump_json(out / "provinces.json", [{"name": "old"}])
        module.freeze_version(out, "a" * 24)
        module.dump_json(out / "provinces.json", [{"name": "new"}])
        module.dump_json(out / "meta.json", {"datasetVersion": "b" * 24})
        module.freeze_version(out, "b" * 24)
        self.assertEqual(json.loads((out / ("v/" + "a" * 24) / "provinces.json").read_text())[0]["name"], "old")

    def test_change_report_detects_cross_shard_reparent_and_rename(self):
        module = load_script("dataset_changes")
        before, after = self.root / "before", self.root / "after"
        for folder, version in ((before, "old"), (after, "new")):
            folder.mkdir()
            (folder / "meta.json").write_text(json.dumps({"datasetVersion": version, "generatedAt": version}))
            (folder / "provinces.json").write_text("[]")
            (folder / "l2").mkdir()
        (before / "l2/110000000000.json").write_text(json.dumps([{"code": "110100000000", "name": "old name", "provinceCode": "110000000000"}]))
        (after / "l2/120000000000.json").write_text(json.dumps([{"code": "110100000000", "name": "new name", "provinceCode": "120000000000"}]))
        with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            summary = module.write_changes(after, before)
        self.assertEqual(summary["counts"]["renamed"]["2"], 1)
        self.assertEqual(summary["counts"]["reparented"]["2"], 1)
        self.assertEqual(summary["counts"]["added"]["2"], 0)
        self.assertEqual(summary["counts"]["removed"]["2"], 0)

    def test_first_change_report_does_not_call_every_record_new(self):
        module = load_script("dataset_changes")
        after = self.root / "after"
        after.mkdir()
        (after / "meta.json").write_text('{"datasetVersion":"new","generatedAt":"today"}')
        (after / "provinces.json").write_text('[{"code":"110000000000","name":"省"}]')
        with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            report = module.write_changes(after, self.root / "missing")
        self.assertTrue(report["initial"])
        self.assertEqual(report["totalEvents"], 0)

    def test_public_refresh_status_uses_active_output_and_omits_private_details(self):
        module = load_script("refresh_report")
        self.write("web/public/data/meta.json", '{"datasetVersion":"new","generatedAt":"new","levels":{}}')
        self.write("web/out/data/meta.json", '{"datasetVersion":"old","generatedAt":"old","levels":{}}')
        module.write_report(self.root, "running")
        report = module.write_report(self.root, "publish_failed", "private/server/path")
        self.assertEqual(report["datasetVersion"], "old")
        self.assertNotIn("private/server/path", (self.root / "web/out/status.json").read_text())
        self.assertEqual(json.loads((self.root / "data/raw/last_refresh.json").read_text())["reason"], "private/server/path")
        self.assertIsNotNone(report["startedAt"])


class PatchTests(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.module = load_script("patches")

    def test_draft_review_and_application_preserve_original_snapshot(self):
        with patch.object(self.module, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            self.module.main(["create", "--code", "110101001001", "--name", "新社区", "--source", "https://example.gov.cn/notice", "--reason", "更名公告", "--effective-date", "2025-01-01"])
            path = next((self.root / "data/patches").glob("*.json"))
            record = json.loads(path.read_text())
            self.assertEqual(record["status"], "draft")
            original = [{"code": "110101001001", "name": "旧社区", "streetCode": "110101001000"}]
            self.assertEqual(self.module.apply_patches([dict(original[0])], path.parent), [])
            self.module.main(["review", record["id"], "--decision", "approved", "--reason", "已核对公开公告"])
            revised = [dict(original[0])]
            public = self.module.apply_patches(revised, path.parent)
            self.assertEqual(revised[0]["name"], "新社区")
            self.assertEqual(original[0]["name"], "旧社区")
            self.assertEqual(revised[0]["patchId"], record["id"])
            self.assertNotIn("createdBy", public[0])
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.module.main(["review", record["id"], "--decision", "rejected", "--reason", "不能覆盖已审核记录"])

    def test_reparent_rejects_unknown_street_and_cross_province_codes(self):
        record = {"id": "b" * 24, "code": "110101001001", "name": "", "streetCode": "110102001000", "reason": "上级调整", "source": "https://example.gov.cn/notice", "effectiveDate": "2025-01-01", "status": "approved", "history": [{"decision": "approved"}]}
        folder = self.root / "data/patches"
        self.module.save(folder / (record["id"] + ".json"), record)
        with self.assertRaisesRegex(ValueError, "当前来源数据"):
            self.module.apply_patches([{"code": record["code"], "streetCode": "110101001000", "name": "村"}], folder, street_codes={"110101001000"})
        record["streetCode"] = "120101001000"
        with self.assertRaisesRegex(ValueError, "同省"):
            self.module.validate(record)

    def test_signed_or_credential_sources_are_rejected(self):
        record = {"id": "a" * 24, "code": "110101001001", "name": "社区", "streetCode": "", "reason": "依据", "source": "https://example.com/?token=sample", "effectiveDate": "2025-01-01", "status": "draft"}
        with self.assertRaises(ValueError):
            self.module.validate(record)


if __name__ == "__main__":
    unittest.main()
