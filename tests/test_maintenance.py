"""维护脚本的离线回归测试；所有写入、调度和失败注入都在临时目录中。"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
        spec.loader.exec_module(module)
    return module


class TemporaryProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="divisions-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project's % copy"
        self.root.mkdir()

    def write(self, relative, text, executable=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        return path

    def python_tool(self, name, source):
        return self.write(f"bin/{name}", f"#!{sys.executable}\n{source}", executable=True)

    def environment(self, **extra):
        return {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                "PY": sys.executable, "PYTHONDONTWRITEBYTECODE": "1", **extra}


class ScheduleTests(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.script = self.write("scripts/schedule.py", (ROOT / "scripts/schedule.py").read_text())
        self.store = self.root / "crontab.txt"
        self.python_tool("crontab", """
import os, sys
from pathlib import Path
p = Path(os.environ['CRON_STORE'])
if sys.argv[1] == '-l':
    if not p.exists():
        print('no crontab for test', file=sys.stderr)
        sys.exit(1)
    print(p.read_text(), end='')
else:
    p.write_text(sys.stdin.read())
""")

    def run_schedule(self, *args, **extra):
        return subprocess.run([sys.executable, str(self.script), *args],
                              cwd=self.tmp.name, env=self.environment(CRON_STORE=str(self.store), **extra),
                              capture_output=True, text=True, timeout=10)

    def test_install_update_and_remove_preserve_other_jobs(self):
        original = "# other job\n10 1 * * * /bin/true\n"
        self.store.write_text(original)
        for _ in range(2):
            result = self.run_schedule("install")
            self.assertEqual(result.returncode, 0, result.stderr)
        content = self.store.read_text()
        self.assertTrue(content.startswith(original))
        self.assertEqual(content.count("# BEGIN china-divisions-explorer"), 1)
        self.assertIn("0 3 15 * *", content)
        self.assertEqual(self.run_schedule("remove").returncode, 0)
        self.assertEqual(self.store.read_text(), original)

    def test_no_existing_crontab_and_dry_run(self):
        result = self.run_schedule("install", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.run_schedule("install").returncode, 0)
        self.assertEqual(self.run_schedule("status").returncode, 0)

    def test_cron_command_handles_spaces_quotes_and_percent(self):
        self.write("scripts/refresh.sh", "#!/bin/bash\nprintf 'scheduled success\\n'\n")
        self.assertEqual(self.run_schedule("install").returncode, 0)
        line = self.store.read_text().splitlines()[1]
        command = line.split(" ", 5)[5].replace("\\%", "%")
        result = subprocess.run(["/bin/sh", "-c", command], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "data/logs/scheduled.log").read_text(), "scheduled success\n")

    def test_invalid_schedule_and_damaged_markers_do_not_write(self):
        self.assertNotEqual(self.run_schedule("install", "--schedule", "99 3 15 * *").returncode, 0)
        self.assertFalse(self.store.exists())
        damaged = "# BEGIN china-divisions-explorer refresh\nother content\n"
        self.store.write_text(damaged)
        self.assertNotEqual(self.run_schedule("install").returncode, 0)
        self.assertEqual(self.store.read_text(), damaged)


class RefreshTests(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.script = self.write("scripts/refresh.sh", (ROOT / "scripts/refresh.sh").read_text())
        self.write("web/public/data/marker", "old")
        self.write("data/raw/mca_level1-4.json", '{"original":true}')
        self.write("scripts/ensure_baseline.py", """
import os, time
from pathlib import Path
if os.environ.get('BASELINE_GATE'):
    Path('gate-ready').touch()
    deadline = time.monotonic() + 8
    while not Path('gate-release').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test gate timed out')
        time.sleep(0.02)
""")
        self.write("scripts/fetch_mca.py", """
import json, os
from pathlib import Path
Path('data/raw/mca_level1-4.json').write_text('{"candidate":true}')
Path('data/raw/_failed.json').write_text(json.dumps(['failed'] * int(os.environ.get('FAILED_COUNT', '0'))))
""")
        self.write("scripts/build_dataset.py", """
import json, os
from pathlib import Path
if os.environ.get('FAIL_DATA_BUILD'):
    raise RuntimeError('injected data build failure')
p = Path(os.environ['DATA_OUT'])
levels = {str(i): {'count': 1, 'asOf': '2025-12-31', 'live': i < 5} for i in range(1, 6)}
(p / 'meta.json').write_text(json.dumps({'levels': levels, 'legacy': {}}))
(p / 'marker').write_text('new')
""")
        self.write("scripts/validate.py", """
import os, sys
from pathlib import Path
p = Path(os.environ['DATA_OUT'])
assert p.name == 'staging'
assert (p / 'marker').read_text() == 'new'
assert Path('web/public/data/marker').read_text() == 'old'
sys.exit(1 if os.environ.get('FAIL_VALIDATE') else 0)
""")
        self.python_tool("curl", "import os\nprint(os.environ.get('PROBE_HTTP', '200'), end='')\n")
        self.python_tool("sleep", "pass\n")
        self.python_tool("npm", "import os, sys\nsys.exit(int(os.environ.get('NPM_STATUS', '0')))\n")
        self.rsync = shutil.which("rsync")
        self.python_tool("rsync", """
import os, subprocess, sys
from pathlib import Path
source, target = sys.argv[-2:]
failure = (os.environ.get('FAIL_BACKUP') and '/snapshots/pre-' in target or
           os.environ.get('FAIL_PUBLISH') and source.endswith('/staging/'))
if failure:
    p = Path(target)
    p.mkdir(parents=True, exist_ok=True)
    (p / 'marker').write_text('partial')
    sys.exit(1)
sys.exit(subprocess.call([os.environ['REAL_RSYNC'], *sys.argv[1:]]))
""")

    def env(self, **extra):
        return self.environment(REAL_RSYNC=self.rsync, SKIP_BUILD="1", PROBE_MAX="1",
                                PROBE_INTERVAL="0", **extra)

    def run_refresh(self, *args, **extra):
        return subprocess.run(["bash", str(self.script), *args], cwd=self.tmp.name,
                              env=self.env(**extra), capture_output=True, text=True, timeout=15)

    def status(self):
        return json.loads((self.root / "data/raw/last_refresh.json").read_text())["status"]

    def assert_original_data(self):
        self.assertEqual((self.root / "web/public/data/marker").read_text(), "old")
        self.assertEqual(json.loads((self.root / "data/raw/mca_level1-4.json").read_text()),
                         {"original": True})

    def test_success_from_unrelated_working_directory(self):
        result = self.run_refresh("--skip-fetch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status(), "ok")
        self.assertEqual((self.root / "web/public/data/marker").read_text(), "new")
        self.assertEqual((self.root / "data/snapshots/latest/marker").read_text(), "new")

    def test_probe_failure_keeps_data(self):
        result = self.run_refresh(PROBE_HTTP="503")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status(), "skipped")
        self.assert_original_data()
        self.assertFalse(list((self.root / "data/snapshots").glob("pre-*")))

    def test_failed_backup_is_never_used_for_rollback(self):
        result = self.run_refresh("--skip-fetch", FAIL_BACKUP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "failed")
        self.assert_original_data()

    def test_validation_failure_restores_raw_before_publication(self):
        result = self.run_refresh(FAIL_VALIDATE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "failed")
        self.assert_original_data()

    def test_data_build_failure_restores_raw(self):
        result = self.run_refresh(FAIL_DATA_BUILD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "failed")
        self.assert_original_data()

    def test_interrupted_publication_restores_complete_backup(self):
        result = self.run_refresh(FAIL_PUBLISH="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "failed")
        self.assert_original_data()

    def test_incomplete_fetch_returns_nonzero_and_restores_raw(self):
        result = self.run_refresh(FAILED_COUNT="6")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "incomplete")
        self.assert_original_data()

    def test_static_build_failure_is_not_reported_as_success(self):
        (self.root / "web/out").mkdir()
        env = self.env(NPM_STATUS="1")
        env["SKIP_BUILD"] = "0"
        result = subprocess.run(["bash", str(self.script), "--skip-fetch"], cwd=self.tmp.name,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "build_failed")
        self.assertEqual((self.root / "web/public/data/marker").read_text(), "new")

    def test_lock_is_held_through_exec_and_released_on_exit(self):
        proc = subprocess.Popen(["bash", str(self.script), "--skip-fetch"], cwd=self.tmp.name,
                                env=self.env(BASELINE_GATE="1"), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "gate-ready").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / "gate-ready").exists())
            result = self.run_refresh("--skip-fetch")
            self.assertEqual(result.returncode, 0)
            self.assertIn("已有刷新任务运行", result.stdout)
            (self.root / "gate-release").touch()
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stdout + stderr)
            with (self.root / "data/.refresh.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.communicate(timeout=10)

    def test_configured_refresh_publishes_new_static_version(self):
        self.write("data/deploy.json", "{}")
        self.write("scripts/publish.py", "from pathlib import Path\nPath('published').write_text('new')\n")
        env = self.env()
        env["SKIP_BUILD"] = "0"
        result = subprocess.run(["bash", str(self.script), "--skip-fetch"], cwd=self.tmp.name,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "published").read_text(), "new")

    def test_publication_failure_is_reported_and_skip_build_never_publishes(self):
        self.write("data/deploy.json", "{}")
        self.write("scripts/publish.py", "raise RuntimeError('injected publish failure')\n")
        env = self.env()
        env["SKIP_BUILD"] = "0"
        result = subprocess.run(["bash", str(self.script), "--skip-fetch"], cwd=self.tmp.name,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status(), "publish_failed")
        # 验证 SKIP_BUILD 单独语义，第二次运行前恢复校验器所预期的旧站点夹具。
        self.write("web/public/data/marker", "old")
        result = self.run_refresh("--skip-fetch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status(), "ok")


class FetchTests(TemporaryProject):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"MCA_AS_OF": "2025-12-31"})
        env.start()
        self.addCleanup(env.stop)

    def test_direct_city_failure_is_tracked_and_virtual_parent_restored(self):
        module = load_script("fetch_mca")
        raw = self.root / "raw"
        raw.mkdir()
        code = "441900000000"
        city = {"code": code, "name": "direct city", "level": 2}
        tree = {"children": [{"code": "440000000000", "name": "province", "level": 1,
                              "children": [city]}]}
        previous = {"areas": [{**city, "level": 3, "cityCode": code, "virtual": True}],
                    "streets": [{"code": "441900001000", "name": "street", "level": 4,
                                 "areaCode": code}]}
        (raw / "mca_level1-4.prev.json").write_text(json.dumps(previous))
        with patch.object(module, "OUT", raw), \
                patch.object(module, "fetch", side_effect=lambda c, _: tree if not c else None), \
                contextlib.redirect_stdout(io.StringIO()):
            module.main()
        self.assertEqual(json.loads((raw / "_failed.json").read_text()), [code])
        result = json.loads((raw / "mca_level1-4.json").read_text())
        self.assertEqual(result["areas"][0]["code"], code)
        self.assertTrue(result["streets"][0]["carriedOver"])

    def test_successful_retry_clears_stale_failed_list(self):
        module = load_script("fetch_mca")
        raw = self.root / "raw"
        raw.mkdir()
        area = {"code": "110101000000", "name": "county", "level": 3}
        city = {"code": "110100000000", "name": "city", "level": 2, "children": [area]}
        province = {"code": "110000000000", "name": "province", "level": 1, "children": [city]}
        tree = {"children": [province]}
        street = {"code": "110101001000", "name": "street", "level": 4}
        with patch.object(module, "OUT", raw), contextlib.redirect_stdout(io.StringIO()):
            with patch.object(module, "fetch", side_effect=lambda code, _: tree if not code else None):
                module.main()
            self.assertEqual(json.loads((raw / "_failed.json").read_text()), [area["code"]])
            with patch.object(module, "fetch", side_effect=lambda code, _: tree if not code else {"children": [street]}):
                module.main()
            self.assertEqual(json.loads((raw / "_failed.json").read_text()), [])
            (raw / "_failed.json").write_text('["stale"]')
            with patch.object(module, "fetch", return_value=tree):
                module.main()
            self.assertEqual(json.loads((raw / "_failed.json").read_text()), [])


class ValidationTests(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.module = load_script("validate")
        self.data = self.root / "candidate"
        levels = {str(i): {"count": 1, "label": f"L{i}", "asOf": "2023-06-30"} for i in range(1, 6)}
        self.write("candidate/meta.json", json.dumps({"levels": levels}))
        self.write("candidate/provinces.json", '[{"code":"110000000000"}]')
        self.write("candidate/l2/110000000000.json", '[{"code":"110100000000"}]')
        self.write("candidate/l3/110100000000.json", '[{"code":"110101000000"}]')
        self.write("candidate/l4/1101.json", '{"110101000000":[{"code":"110101001000"}]}')
        self.write("candidate/l5/1101.json", '{"110101001000":[{"code":"110101001001","streetCode":"110101001000"}]}')
        self.write("data/raw/_failed.json", "[]")
        for name, value in {"ROOT": self.root, "DATA": self.data,
                            "PREV": self.root / "previous", "MIN_SHARDS": {}}.items():
            mock = patch.object(self.module, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def validate(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.module.main()

    def test_complete_candidate_passes(self):
        self.assertEqual(self.validate(), 0)

    def test_missing_parent_shard_is_counted_as_unattached(self):
        (self.data / "l4/1101.json").unlink()
        meta = json.loads((self.data / "meta.json").read_text())
        meta["levels"]["4"]["count"] = 1
        self.write("candidate/l4/9999.json", '{"other":[{"code":"999999001000"}]}')
        self.assertEqual(self.validate(), 1)

    def test_meta_count_cannot_hide_missing_records(self):
        self.write("candidate/l5/1101.json", "{}")
        self.assertEqual(self.validate(), 1)

    def test_corrupt_failed_list_is_rejected(self):
        self.write("data/raw/_failed.json", "broken")
        self.assertEqual(self.validate(), 1)

    def test_failed_county_requires_actual_recovery(self):
        self.write("data/raw/_failed.json", '["110101000000"]')
        self.write("data/raw/mca_level1-4.json", '{"streets":[]}')
        self.assertEqual(self.validate(), 1)
        self.write("data/raw/mca_level1-4.json", '{"streets":[{"areaCode":"110101000000","carriedOver":true}]}')
        self.assertEqual(self.validate(), 0)


class BaselineTests(TemporaryProject):
    def test_invalid_download_preserves_existing_baseline(self):
        module = load_script("ensure_baseline")
        target = self.write("cache/divisions-2023.csv", "existing baseline")

        def pack(*args, **kwargs):
            content = b"code,name\n1,first\n"
            with tarfile.open(Path(kwargs["cwd"]) / "cndiv-source-2023-test.tgz", "w:gz") as tf:
                member = tarfile.TarInfo("package/data/divisions.csv")
                member.size = len(content)
                tf.addfile(member, io.BytesIO(content))

        with patch.object(module, "CACHE", target.parent), patch.object(module, "TARGET", target), \
                patch.object(module, "EXPECTED_ROWS", 2), patch.object(module.subprocess, "run", side_effect=pack):
            with self.assertRaises(RuntimeError):
                module.download()
            self.assertEqual(target.read_text(), "existing baseline")

    def test_valid_download_replaces_baseline_after_validation(self):
        module = load_script("ensure_baseline")
        target = self.write("cache/divisions-2023.csv", "existing baseline")
        content = b"code,name\n1,first\n2,second\n"

        def pack(*args, **kwargs):
            with tarfile.open(Path(kwargs["cwd"]) / "cndiv-source-2023-test.tgz", "w:gz") as tf:
                member = tarfile.TarInfo("package/data/divisions.csv")
                member.size = len(content)
                tf.addfile(member, io.BytesIO(content))

        with patch.object(module, "CACHE", target.parent), patch.object(module, "TARGET", target), \
                patch.object(module, "EXPECTED_ROWS", 2), patch.object(module, "EXPECTED_SHA256", __import__("hashlib").sha256(content).hexdigest()), patch.object(module.subprocess, "run", side_effect=pack):
            module.download()
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(list(target.parent.glob("baseline-*")), [])


class FallbackTests(TemporaryProject):
    def test_nbs_uses_project_baseline_and_clears_old_failures(self):
        with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            module = load_script("fetch_nbs_fallback")
        csv = self.write("cache/divisions-2023.csv",
                         "code,name,level,parent_code\n110000000000,province,1,\n")
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "_failed.json").write_text('["old failure"]')
        with patch.object(module, "OUT", raw), patch.object(module, "NBS2023_CSV", csv), \
                patch.object(module, "ensure_baseline", return_value=0), \
                contextlib.redirect_stdout(io.StringIO()):
            module.main()
        data = json.loads((raw / "mca_level1-4.json").read_text())
        self.assertEqual(data["provinces"][0]["code"], "110000000000")
        self.assertEqual(json.loads((raw / "_failed.json").read_text()), [])
        self.assertNotIn("/tmp/", data["meta"]["endpoint"])

    def test_mumuy_supports_explicit_source_directory(self):
        module = load_script("fetch_mumuy_fallback")
        self.write("source/list.json", '{"110000":"province","110100":"city","110101":"county"}')
        self.write("source/code/110101.json", '{"110101001":"street"}')
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "_failed.json").write_text('["old failure"]')
        args = ["fetch_mumuy_fallback.py", "--source-dir", str(self.root / "source"),
                "--as-of", "2026-04"]
        with patch.object(module, "OUT", raw), patch.object(sys, "argv", args), \
                contextlib.redirect_stdout(io.StringIO()):
            module.main()
        data = json.loads((raw / "mca_level1-4.json").read_text())
        self.assertEqual(data["streets"][0]["code"], "110101001000")
        self.assertEqual(data["meta"]["asOf"], "2026-04")
        self.assertEqual(json.loads((raw / "_failed.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()
