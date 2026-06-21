from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "ops" / "scripts" / "report_environment_parity.py"
spec = importlib.util.spec_from_file_location("report_environment_parity", SCRIPT_PATH)
assert spec and spec.loader
env_parity = importlib.util.module_from_spec(spec)
sys.modules["report_environment_parity"] = env_parity
spec.loader.exec_module(env_parity)


class EnvironmentParityReportTests(unittest.TestCase):
    def test_parse_env_file_strips_quotes_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "local.env"
            env_file.write_text(
                "\n".join(
                    [
                        "# comment",
                        "BRP_BACKEND_PORT=8001",
                        "BRP_TRAFFIC_STATUS_MARKETS='CN,BK'",
                        'APP_ENV="production"',
                    ]
                ),
                encoding="utf-8",
            )

            values = env_parity.parse_env_file(env_file)

        self.assertEqual(values["BRP_BACKEND_PORT"], "8001")
        self.assertEqual(values["BRP_TRAFFIC_STATUS_MARKETS"], "CN,BK")
        self.assertEqual(values["APP_ENV"], "production")

    def test_infer_environment_from_root_path(self) -> None:
        self.assertEqual(env_parity.infer_environment(Path("/opt/brp/staging/app")), "cn-staging")
        self.assertEqual(env_parity.infer_environment(Path("/opt/brp/prod/app")), "cn-prod")

    def test_expected_market_scope_uses_configured_scope(self) -> None:
        scope, source = env_parity.expected_market_scope(
            {"BRP_TRAFFIC_STATUS_MARKETS": "kr"},
            env_parity.ENVIRONMENT_SPECS["kr-prod"],
        )

        self.assertEqual(scope, ("KR",))
        self.assertEqual(source, "BRP_TRAFFIC_STATUS_MARKETS")

    def test_dist_contains_marker_uses_current_dist_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            assets = root / "apps" / "web" / "dist" / "assets"
            assets.mkdir(parents=True)
            (assets / "index-test.js").write_text("window.__APP_VERSION__='abc1234';", encoding="utf-8")
            old_assets = root / "apps" / "web" / "dist.prev-old" / "assets"
            old_assets.mkdir(parents=True)
            (old_assets / "index-old.js").write_text("abc1234", encoding="utf-8")

            ok, message = env_parity.dist_contains_marker(root, "abc1234")

        self.assertTrue(ok)
        self.assertIn("apps/web/dist/assets/index-test.js", message)

    def test_legacy_proxy_file_check_flags_present_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "ops" / "scripts").mkdir(parents=True)
            (root / "apps" / "web" / "dist" / "assets").mkdir(parents=True)
            (root / "apps" / "web" / "dist" / "assets" / "index-test.js").write_text(
                "abc1234",
                encoding="utf-8",
            )
            (root / "ops" / "scripts" / "serve_react_static.py").write_text("# old", encoding="utf-8")
            (root / "ops" / "cloudflared").mkdir(parents=True)
            (root / "ops" / "cloudflared" / "kr-config.example.yml").write_text(
                "service: http://127.0.0.1:8501\n",
                encoding="utf-8",
            )
            env_file = root / "ops" / "env" / "local.env"
            env_file.parent.mkdir(parents=True)
            env_file.write_text(
                "\n".join(
                    [
                        "BRP_BACKEND_PORT=8001",
                        "BRP_TRAFFIC_STATUS_MARKETS=BK,CN,KR",
                        "APP_ENV=staging",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch.object(env_parity, "git_head", return_value=("abc1234", None)),
                patch.object(env_parity, "git_origin_head", return_value=("abc1234", None)),
                patch.object(env_parity, "backend_health", return_value={"healthy": True, "status_code": 200}),
                patch.object(env_parity, "frontend_origin_status", return_value={"alive": True, "status_code": 401}),
            ):
                report = env_parity.build_report(
                    environment="cn-staging",
                    root_dir=root,
                    env_file=env_file,
                    expected_head="abc1234",
                )

        legacy_check = next(
            row for row in report["checks"] if row["name"] == "legacy_file:ops/scripts/serve_react_static.py"
        )
        self.assertEqual(legacy_check["status"], "fail")


if __name__ == "__main__":
    unittest.main()
