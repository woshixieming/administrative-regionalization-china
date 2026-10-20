import contextlib
import io
import json
import http.server
import functools
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_maintenance import ROOT, TemporaryProject

with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    import deploy
    import publish


class DeploymentFixture(TemporaryProject):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
        self.destination = Path(self.tmp.name).resolve() / "published site"
        self.nginx = Path(self.tmp.name).resolve() / "nginx/site.conf"
        self.write("deploy/nginx.conf.template", (ROOT / "deploy/nginx.conf.template").read_text())
        self.write("web/out/index.html", "<html>中国行政区划</html>")
        self.write("web/out/404.html", "not found")
        self.write("web/out/_next/static/app.css", "body{color:green}")
        self.write("web/out/data/provinces.json", "[]")
        self.version("one")

    def version(self, value):
        meta = json.dumps({"generatedAt": value})
        self.write("web/out/data/meta.json", meta)
        self.write("web/public/data/meta.json", meta)
        self.write("web/out/data/search/manifest.json", meta)
        self.write("web/public/data/search/manifest.json", meta)

    def activate(self):
        release = publish.prepare_release(self.root, self.destination)
        publish.activate_release(self.root, self.destination, release)
        return release


class PublishTests(DeploymentFixture):
    def test_http_health_check_requires_fixed_dataset_route(self):
        version = "c" * 24
        value = {"generatedAt": "fixed", "datasetVersion": version}
        for name in ("web/out/data/meta.json", "web/public/data/meta.json", "web/out/data/search/manifest.json", f"web/out/data/v/{version}/meta.json"):
            self.write(name, json.dumps(value))
        release = self.activate()
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        handler = functools.partial(QuietHandler, directory=str(self.destination / "current"))
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = {"publishRoot": str(self.destination), "port": server.server_port, "domain": "localhost"}
                publish.health_check(config)
                (release / f"data/v/{version}/meta.json").unlink()
                with self.assertRaises(OSError):
                    publish.health_check(config)
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_old_hashed_assets_survive_switch_but_do_not_accumulate_forever(self):
        for index in range(4):
            if index:
                (self.root / f"web/out/_next/static/build-{index - 1}.js").unlink()
            self.write(f"web/out/_next/static/build-{index}.js", str(index))
            self.version(str(index))
            release = self.activate()
        self.assertTrue((release / "_next/static/build-1.js").exists())
        self.assertFalse((release / "_next/static/build-0.js").exists())
        self.assertCountEqual(json.loads((release / publish.RELEASE_FILE).read_text())["assets"], ["app.css", "build-3.js"])

    def test_previous_dataset_is_available_under_its_own_version_url(self):
        def frozen(version):
            value = {"generatedAt": version, "datasetVersion": version}
            self.version(version)
            self.write("web/out/data/meta.json", json.dumps(value))
            self.write("web/public/data/meta.json", json.dumps(value))
            self.write("web/out/data/search/manifest.json", json.dumps(value))
            self.write(f"web/out/data/v/{version}/meta.json", json.dumps(value))
        first = "a" * 24
        second = "b" * 24
        frozen(first)
        self.activate()
        publish.remove_tree(self.root / f"web/out/data/v/{first}")
        frozen(second)
        release = self.activate()
        self.assertEqual(json.loads((release / f"data/v/{first}/meta.json").read_text())["datasetVersion"], first)
        self.assertEqual(json.loads((release / "data/meta.json").read_text())["datasetVersion"], second)

    def test_release_switch_preserves_previous_files_and_readable_permissions(self):
        first = self.activate()
        self.version("two")
        second = self.activate()
        self.assertEqual(publish.current_release(self.root, self.destination), second)
        self.assertEqual(json.loads((first / "data/meta.json").read_text())["generatedAt"], "one")
        self.assertEqual((second / "index.html").stat().st_mode & 0o777, 0o644)
        self.assertEqual(second.stat().st_mode & 0o777, 0o755)
        publish.restore_release(self.root, self.destination, first)
        self.assertEqual(publish.current_release(self.root, self.destination), first)

    def test_reject_foreign_or_nonempty_directories(self):
        self.destination.mkdir()
        (self.destination / "existing").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "非空"):
            self.activate()
        self.assertEqual((self.destination / "existing").read_text(), "keep")
        (self.destination / publish.OWNER_FILE).write_text(json.dumps({"projectRoot": "/another-project"}))
        with self.assertRaisesRegex(RuntimeError, "其他项目"):
            self.activate()

    def test_stale_build_is_never_published(self):
        first = self.activate()
        self.write("web/public/data/meta.json", '{"generatedAt":"new-data"}')
        with self.assertRaisesRegex(RuntimeError, "不是当前数据版本"):
            publish.prepare_release(self.root, self.destination)
        self.assertEqual(publish.current_release(self.root, self.destination), first)

    def test_copy_failure_leaves_current_unchanged_and_removes_partial_version(self):
        first = self.activate()

        def broken_copy(source, target):
            target.mkdir()
            (target / "partial").write_text("partial")
            raise OSError("injected copy failure")

        with patch.object(publish.shutil, "copytree", side_effect=broken_copy):
            with self.assertRaises(OSError):
                publish.prepare_release(self.root, self.destination)
        self.assertEqual(publish.current_release(self.root, self.destination), first)
        self.assertEqual(list((self.destination / "releases").glob(".building-*")), [])

    def test_only_managed_old_releases_are_pruned(self):
        for version in ("one", "two", "three", "four"):
            self.version(version)
            self.activate()
        unrelated = self.destination / "releases/release-unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_text("keep")
        publish.prune_releases(self.root, self.destination)
        managed = [p for p in (self.destination / "releases").iterdir() if (p / publish.RELEASE_FILE).exists()]
        self.assertEqual(len(managed), 3)
        self.assertEqual((unrelated / "keep").read_text(), "keep")
        self.assertEqual(json.loads((self.destination / "current/data/meta.json").read_text())["generatedAt"], "four")

    def test_current_directory_and_external_symlink_are_not_overwritten(self):
        self.destination.mkdir()
        (self.destination / publish.OWNER_FILE).write_text(json.dumps({"projectRoot": str(self.root)}))
        current = self.destination / "current"
        current.mkdir()
        with self.assertRaisesRegex(RuntimeError, "不是软链接"):
            publish.current_release(self.root, self.destination)
        current.rmdir()
        current.symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, "非本项目"):
            publish.current_release(self.root, self.destination)

    def test_refresh_lock_prevents_simultaneous_deployment(self):
        with publish.project_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "已有部署"):
                with publish.project_lock(self.root):
                    pass

    def test_automatic_publication_health_failure_restores_previous_version(self):
        previous = self.activate()
        self.version("two")
        with patch.object(publish, "health_check", side_effect=RuntimeError("injected HTTP failure")):
            with self.assertRaises(RuntimeError):
                publish.publish(self.root, {"publishRoot": str(self.destination)})
        self.assertEqual(publish.current_release(self.root, self.destination), previous)


class DeployTests(DeploymentFixture):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.failure = None
        self.nginx_tests = 0
        self.cron = "# unrelated\n0 1 * * * /bin/true\n"
        self.old_cron = self.cron
        self.previous = self.activate()
        self.version("two")
        self.nginx.parent.mkdir()
        with patch.object(deploy, "ROOT", self.root):
            self.old_nginx = deploy.render_config("old.example.com", 80, self.destination)
        self.nginx.write_text(self.old_nginx)
        self.old_runtime = json.dumps({"version": 1, "projectRoot": str(self.root),
            "publishRoot": str(self.destination), "domain": "old.example.com", "port": 80})
        self.write("data/deploy.json", self.old_runtime)
        for mock in (
            patch.object(deploy, "ROOT", self.root),
            patch.object(deploy.platform, "system", return_value="Linux"),
            patch.object(deploy.shutil, "which", side_effect=lambda name: "/usr/bin/" + name),
            patch.object(deploy, "run", side_effect=self.fake_run),
            patch.object(deploy, "health_check", side_effect=self.fake_health),
            patch.object(deploy.schedule, "read_crontab", side_effect=lambda: self.cron),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def fake_health(self, config):
        if self.failure == "health":
            raise RuntimeError("injected HTTP failure")
        self.assertEqual(json.loads((self.destination / "current/data/meta.json").read_text())["generatedAt"], "two")

    def fake_run(self, command, capture=False, check=True, input=None, **kwargs):
        args = [str(a) for a in command]
        if args[0] == "sudo":
            args = args[1:]
        self.calls.append(args)
        result = subprocess.CompletedProcess(args, 0, "", "")
        if args == ["node", "--version"]:
            result.stdout = "v22.17.1\n"
        elif args[:2] == ["install", "-d"]:
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
        elif args[:2] == ["install", "-m"]:
            shutil.copyfile(args[-2], args[-1])
        elif args[:2] == ["rm", "-f"]:
            Path(args[-1]).unlink(missing_ok=True)
        elif args == ["nginx", "-t"]:
            self.nginx_tests += 1
            if self.failure == "nginx" and self.nginx_tests == 2:
                raise subprocess.CalledProcessError(1, args)
        elif args == ["nginx", "-T"]:
            result.stdout = f"# configuration file {self.nginx}:\n{self.nginx.read_text()}"
        elif args[:2] == ["systemctl", "list-unit-files"]:
            result.stdout = "cron.service enabled\n"
        elif args == ["npm", "run", "build"] and self.failure == "build":
            raise subprocess.CalledProcessError(1, args)
        elif len(args) > 1 and args[1].endswith("schedule.py"):
            self.cron += deploy.schedule.BEGIN + "\n0 3 15 * * new-job\n" + deploy.schedule.END + "\n"
            if self.failure == "cron":
                self.cron += "# concurrently added\n5 2 * * * /bin/true\n"
                raise subprocess.CalledProcessError(1, args)
        elif args == ["crontab", "-"]:
            self.cron = input
        return result

    def execute(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return deploy.main(["--domain", "regions.example.com", "--publish-root", str(self.destination),
                                "--nginx-conf", str(self.nginx), *extra])

    def assert_previous(self):
        self.assertEqual(publish.current_release(self.root, self.destination), self.previous)
        self.assertEqual(self.nginx.read_text(), self.old_nginx)
        self.assertEqual((self.root / "data/deploy.json").read_text(), self.old_runtime)

    def test_full_deployment_switches_version_and_installs_current_user_cron(self):
        self.assertEqual(self.execute(), 0)
        self.assertNotEqual(publish.current_release(self.root, self.destination), self.previous)
        self.assertIn("server_name regions.example.com;", self.nginx.read_text())
        self.assertEqual(publish.read_config(self.root)["domain"], "regions.example.com")
        self.assertTrue(self.cron.startswith(self.old_cron))
        self.assertIn(deploy.schedule.BEGIN, self.cron)

    def test_config_snapshots_are_read_only_after_acquiring_deployment_lock(self):
        original = deploy.check_config_owner
        def guarded(path):
            with self.assertRaisesRegex(RuntimeError, "已有部署"):
                with publish.project_lock(self.root):
                    pass
            return original(path)
        with patch.object(deploy, "check_config_owner", side_effect=guarded):
            self.assertEqual(self.execute(), 0)

    def test_other_deployer_cannot_commit_during_preflight_then_lose_its_config(self):
        original = self.fake_run
        denied = []
        def interleaved(command, **kwargs):
            if list(map(str, command)) == ["node", "--version"]:
                try:
                    with publish.project_lock(self.root):
                        self.nginx.write_text("concurrent commit")
                except RuntimeError:
                    denied.append(True)
            return original(command, **kwargs)
        self.failure = "cron"
        with patch.object(deploy, "run", side_effect=interleaved), self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assertEqual(denied, [True])
        self.assert_previous()

    def test_nginx_validation_failure_restores_config_before_switch(self):
        self.failure = "nginx"
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assert_previous()
        self.assertEqual(self.cron, self.old_cron)

    def test_failed_build_does_not_touch_live_site(self):
        self.failure = "build"
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assert_previous()

    def test_http_failure_restores_previous_live_version(self):
        self.failure = "health"
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_previous()

    def test_partial_cron_install_failure_preserves_concurrently_added_jobs(self):
        self.failure = "cron"
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assert_previous()
        self.assertEqual(self.cron, self.old_cron + "# concurrently added\n5 2 * * * /bin/true\n")

    def test_no_cron_preserves_existing_jobs(self):
        self.assertEqual(self.execute("--no-cron"), 0)
        self.assertEqual(self.cron, self.old_cron)
        self.assertFalse(any(len(c) > 1 and c[1].endswith("schedule.py") for c in self.calls))

    def test_dependency_installation_only_installs_missing_tools(self):
        with patch.object(deploy.shutil, "which", side_effect=lambda name: "/usr/bin/" + name if name in ("apt-get", "nginx") else None):
            deploy.install_os_dependencies(False)
        installs = [c for c in self.calls if c[:2] == ["apt-get", "install"]]
        self.assertEqual(installs, [["apt-get", "install", "-y", "rsync", "curl"]])

    def test_unmanaged_nginx_config_is_refused_before_any_system_operation(self):
        self.nginx.write_text("other application")
        with self.assertRaisesRegex(RuntimeError, "不属于本项目"):
            self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.nginx.read_text(), "other application")

    def test_manual_nginx_changes_are_not_silently_overwritten(self):
        self.nginx.write_text(self.old_nginx + "# user TLS settings\n")
        with self.assertRaisesRegex(RuntimeError, "手工修改"):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_keep_nginx_preserves_manual_configuration_on_upgrades(self):
        manual = self.old_nginx + "# user TLS settings\n"
        self.nginx.write_text(manual)
        with contextlib.redirect_stdout(io.StringIO()):
            result = deploy.main(["--domain", "old.example.com", "--publish-root", str(self.destination),
                                  "--nginx-conf", str(self.nginx), "--keep-nginx", "--no-cron"])
        self.assertEqual(result, 0)
        self.assertEqual(self.nginx.read_text(), manual)
        # 保存的指纹仍代表自动生成配置，下一次无保留参数时继续拒绝覆盖手工设置。
        with self.assertRaisesRegex(RuntimeError, "手工修改"):
            self.execute()

    def test_dry_run_and_invalid_arguments_do_not_mutate_system(self):
        self.assertEqual(self.execute("--dry-run"), 0)
        self.assertEqual(self.calls, [])
        with self.assertRaises(SystemExit):
            self.execute("--domain", "example.com;include bad")
        with self.assertRaises(SystemExit):
            self.execute("--port", "0")
        with self.assertRaises(SystemExit):
            self.execute("--publish-root", '/var/www/bad"root')
        self.assert_previous()


if __name__ == "__main__":
    unittest.main()
