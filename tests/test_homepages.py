from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apply_homepages  # noqa: E402
from repository_config import ConfigError, load_repositories  # noqa: E402


class ConfigTestCase(unittest.TestCase):
    def load(self, repositories):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repositories.json"
            path.write_text(json.dumps({"repositories": repositories}))
            return load_repositories(path)

    def assert_invalid(self, repositories, message):
        with self.assertRaisesRegex(ConfigError, message):
            self.load(repositories)

    def test_valid_configuration_loads(self):
        repositories = self.load(
            [
                {
                    "name": "example/project",
                    "homepage": "https://example.com",
                    "protected": True,
                }
            ]
        )

        self.assertEqual(repositories[0].name, "example/project")
        self.assertEqual(repositories[0].homepage, "https://example.com")
        self.assertTrue(repositories[0].protected)

    def test_explicit_null_homepage_is_accepted(self):
        repositories = self.load([{"name": "example/project", "homepage": None}])

        self.assertIsNone(repositories[0].homepage)

    def test_missing_homepage_is_rejected(self):
        self.assert_invalid([{"name": "example/project"}], "missing required field")

    def test_empty_or_whitespace_homepage_is_rejected(self):
        for homepage in ("", " ", "\t\n"):
            with self.subTest(homepage=repr(homepage)):
                self.assert_invalid(
                    [{"name": "example/project", "homepage": homepage}],
                    "non-empty string or null",
                )

    def test_invalid_protected_type_is_rejected(self):
        self.assert_invalid(
            [
                {
                    "name": "example/project",
                    "homepage": None,
                    "protected": "true",
                }
            ],
            "must be a boolean",
        )

    def test_duplicate_names_are_rejected_case_insensitively(self):
        self.assert_invalid(
            [
                {"name": "Example/Project", "homepage": None},
                {"name": "example/project", "homepage": None},
            ],
            "duplicate repository name",
        )

    def test_malformed_repository_names_are_rejected(self):
        for name in ("", "project", "/project", "owner/", "owner/two/repos"):
            with self.subTest(name=name):
                self.assert_invalid(
                    [{"name": name, "homepage": None}],
                    "invalid repository name",
                )


class AuthenticationTestCase(unittest.TestCase):
    def test_stored_auth_removes_token_environment_variables(self):
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit"},
            clear=True,
        ):
            environment = apply_homepages.gh_environment(True)

        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("GH_TOKEN", environment)

    def test_default_auth_preserves_token_environment_variables(self):
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit"},
            clear=True,
        ):
            environment = apply_homepages.gh_environment(False)

        self.assertEqual(environment["GITHUB_TOKEN"], "integration")
        self.assertEqual(environment["GH_TOKEN"], "explicit")


class ApplySafetyTestCase(unittest.TestCase):
    def run_apply(self, repositories, *arguments, get_homepage):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps({"repositories": repositories}))
            with (
                patch.object(apply_homepages, "CONFIG_PATH", config_path),
                patch.object(
                    apply_homepages, "get_homepage", side_effect=get_homepage
                ),
                patch.object(apply_homepages, "set_homepage") as set_homepage,
                patch.object(sys, "argv", ["apply_homepages.py", *arguments]),
            ):
                with redirect_stdout(io.StringIO()):
                    result = apply_homepages.main()
        return result, set_homepage

    def test_protected_drift_is_never_written_and_returns_nonzero(self):
        result, set_homepage = self.run_apply(
            [
                {
                    "name": "example/protected",
                    "homepage": "https://expected.example",
                    "protected": True,
                }
            ],
            "--apply",
            get_homepage=["https://current.example"],
        )

        self.assertNotEqual(result, 0)
        set_homepage.assert_not_called()

    def test_preflight_failure_results_in_zero_writes(self):
        failure = subprocess.CalledProcessError(
            1, ["gh"], stderr="simulated read failure"
        )
        result, set_homepage = self.run_apply(
            [
                {"name": "example/first", "homepage": None},
                {"name": "example/second", "homepage": None},
            ],
            "--apply",
            get_homepage=["https://stale.example", failure],
        )

        self.assertNotEqual(result, 0)
        set_homepage.assert_not_called()

    def test_dry_run_results_in_zero_writes(self):
        result, set_homepage = self.run_apply(
            [{"name": "example/project", "homepage": None}],
            get_homepage=["https://stale.example"],
        )

        self.assertEqual(result, 0)
        set_homepage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
