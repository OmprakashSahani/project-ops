from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apply_homepages  # noqa: E402
import audit_homepages  # noqa: E402
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

    def test_unknown_fields_are_rejected(self):
        for field in ("protectd", "homepag", "unexpected"):
            with self.subTest(field=field):
                self.assert_invalid(
                    [{"name": "example/project", "homepage": None, field: True}],
                    f"unknown fields: {field}",
                )

    def test_empty_repositories_is_rejected(self):
        self.assert_invalid([], "must contain at least one repository")

    def test_krsna_remains_protected(self):
        repositories = load_repositories(SCRIPTS.parent / "config/repositories.json")
        krsna = next(
            repository for repository in repositories
            if repository.name == "OmprakashSahani/Krsna"
        )
        self.assertTrue(krsna.protected)
        self.assertEqual(
            krsna.homepage,
            "https://krsna-supreme-personality-of-godhead.vercel.app/",
        )

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

    def test_get_homepage_passes_selected_environment_to_subprocess(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin"
        }
        for module in (audit_homepages, apply_homepages):
            for stored_auth in (False, True):
                with self.subTest(module=module.__name__, stored_auth=stored_auth):
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch("subprocess.run") as run,
                    ):
                        run.return_value.stdout = "https://example.com\n"
                        result = module.get_homepage("example/project", stored_auth)

                        expected_env = {"PATH": "/bin"} if stored_auth else environment
                        run.assert_called_once_with(
                            ["gh", "api", "repos/example/project", "--jq",
                             ".homepage // empty"],
                            check=True, capture_output=True, text=True, env=expected_env,
                        )
                        self.assertEqual(result, "https://example.com")
                        self.assertEqual(dict(os.environ), environment)

    def test_set_homepage_passes_selected_environment_and_explicit_null(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin"
        }
        for stored_auth in (False, True):
            for homepage in (None, "https://example.com"):
                with self.subTest(stored_auth=stored_auth, homepage=homepage):
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch("subprocess.run") as run,
                    ):
                        apply_homepages.set_homepage(
                            "example/project", homepage, stored_auth
                        )
                        expected_env = {"PATH": "/bin"} if stored_auth else environment
                        run.assert_called_once_with(
                            ["gh", "repo", "edit", "example/project", "--homepage",
                             "" if homepage is None else homepage],
                            check=True, env=expected_env,
                        )
                        self.assertEqual(dict(os.environ), environment)


class ApplySafetyTestCase(unittest.TestCase):
    def run_apply(self, repositories, *arguments, get_homepage, write_effect=None):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps({"repositories": repositories}))
            with (
                patch.object(apply_homepages, "CONFIG_PATH", config_path),
                patch.object(
                    apply_homepages, "get_homepage", side_effect=get_homepage
                ) as get_homepage_mock,
                patch.object(
                    apply_homepages, "set_homepage", side_effect=write_effect
                ) as set_homepage,
                patch.object(sys, "argv", ["apply_homepages.py", *arguments]),
            ):
                with redirect_stdout(io.StringIO()) as output:
                    result = apply_homepages.main()
                self.output = output.getvalue()
                self.reads = get_homepage_mock.call_args_list
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
                {"name": "example/third", "homepage": None},
            ],
            "--apply",
            get_homepage=["https://stale.example", failure, None],
        )

        self.assertNotEqual(result, 0)
        set_homepage.assert_not_called()
        self.assertEqual(len(self.reads), 3)

    def test_protected_drift_blocks_unprotected_changes_after_all_reads(self):
        for arguments in ((), ("--apply",)):
            with self.subTest(arguments=arguments):
                result, set_homepage = self.run_apply(
                    [
                        {"name": "example/ordinary", "homepage": None},
                        {"name": "example/protected", "homepage": None,
                         "protected": True},
                        {"name": "example/last", "homepage": None},
                    ],
                    *arguments,
                    get_homepage=["https://stale.example"] * 3,
                )

                self.assertNotEqual(result, 0)
                set_homepage.assert_not_called()
                self.assertEqual(self.reads, [
                    call("example/ordinary", False),
                    call("example/protected", False),
                    call("example/last", False),
                ])
                self.assertIn("example/protected: REFUSED (protected)", self.output)
                self.assertIn("no repositories were modified", self.output)

    def test_partial_failure_reports_updated_failed_and_pending(self):
        failure = subprocess.CalledProcessError(1, ["gh", "repo", "edit"])
        result, set_homepage = self.run_apply(
            [{"name": f"example/{name}", "homepage": None}
             for name in ("first", "second", "third", "fourth")],
            "--apply",
            get_homepage=["https://stale.example"] * 4,
            write_effect=[None, failure],
        )

        self.assertNotEqual(result, 0)
        self.assertEqual(set_homepage.call_args_list, [
            call("example/first", None, False),
            call("example/second", None, False),
        ])
        self.assertIn("Updated: example/first\n", self.output)
        self.assertIn("Failed: example/second\n", self.output)
        self.assertIn(
            "Pending (not attempted): example/third, example/fourth\n", self.output
        )

    def test_dry_run_results_in_zero_writes(self):
        result, set_homepage = self.run_apply(
            [{"name": "example/project", "homepage": None}],
            get_homepage=["https://stale.example"],
        )

        self.assertEqual(result, 0)
        set_homepage.assert_not_called()


class CliIntegrationTestCase(unittest.TestCase):
    """Exercise real CLI parsing, config loading, and helpers with gh mocked."""

    def run_cli(self, module, repositories, arguments, responses):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps({"repositories": repositories}))
            with (
                patch.object(module, "CONFIG_PATH", config_path),
                patch.object(sys, "argv", [module.__file__, *arguments]),
                patch("subprocess.run", side_effect=responses) as run,
                redirect_stdout(io.StringIO()) as output,
                redirect_stderr(io.StringIO()),
            ):
                result = module.main()
        return result, run.call_args_list, output.getvalue()

    def test_auth_modes_work_through_both_cli_main_functions(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin"
        }
        for module in (audit_homepages, apply_homepages):
            for stored_auth in (False, True):
                with self.subTest(module=module.__name__, stored_auth=stored_auth):
                    arguments = ["--use-stored-gh-auth"] if stored_auth else []
                    responses = [subprocess.CompletedProcess([], 0, "https://old.example\n")]
                    expected_commands = [
                        ["gh", "api", "repos/example/project", "--jq", ".homepage // empty"]
                    ]
                    if module is apply_homepages:
                        arguments.append("--apply")
                        responses.append(subprocess.CompletedProcess([], 0))
                        expected_commands.append(
                            ["gh", "repo", "edit", "example/project", "--homepage", ""]
                        )
                    with patch.dict(os.environ, environment, clear=True):
                        result, calls, _ = self.run_cli(
                            module, [{"name": "example/project", "homepage": None}],
                            arguments, responses,
                        )
                    self.assertEqual(result, 0 if module is apply_homepages else 1)
                    self.assertEqual([c.args[0] for c in calls], expected_commands)
                    expected_env = {"PATH": "/bin"} if stored_auth else environment
                    for subprocess_call in calls:
                        self.assertEqual(subprocess_call.kwargs["env"], expected_env)

    def test_apply_completes_all_reads_before_any_write(self):
        result, calls, _ = self.run_cli(
            apply_homepages,
            [{"name": f"example/{name}", "homepage": None}
             for name in ("first", "second", "third")],
            ["--apply"],
            [subprocess.CompletedProcess([], 0, "https://old.example\n")] * 3
            + [subprocess.CompletedProcess([], 0)] * 3,
        )
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", f"repos/example/{name}", "--jq", ".homepage // empty"]
            for name in ("first", "second", "third")
        ] + [
            ["gh", "repo", "edit", f"example/{name}", "--homepage", ""]
            for name in ("first", "second", "third")
        ])

    def test_dry_run_only_invokes_read_commands(self):
        result, calls, output = self.run_cli(
            apply_homepages, [{"name": "example/project", "homepage": None}], [],
            [subprocess.CompletedProcess([], 0, "https://old.example\n")],
        )
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", "repos/example/project", "--jq", ".homepage // empty"]
        ])
        self.assertIn("WOULD CHANGE", output)

    def test_audit_only_reads_including_protected_drift_and_read_failure(self):
        result, calls, output = self.run_cli(
            audit_homepages,
            [
                {"name": "example/ordinary", "homepage": None},
                {"name": "example/protected", "homepage": None, "protected": True},
                {"name": "example/error", "homepage": None},
                {"name": "example/matching", "homepage": None},
            ], [],
            [
                subprocess.CompletedProcess([], 0, "https://old.example\n"),
                subprocess.CompletedProcess([], 0, "https://old.example\n"),
                subprocess.CalledProcessError(1, ["gh"], stderr="simulated failure"),
                subprocess.CompletedProcess([], 0, ""),
            ],
        )
        self.assertNotEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", f"repos/example/{name}", "--jq", ".homepage // empty"]
            for name in ("ordinary", "protected", "error", "matching")
        ])
        for status in ("STALE", "REFUSED (protected)", "ERROR", "OK"):
            self.assertIn(status, output)

    def test_invalid_config_never_invokes_gh_in_either_cli(self):
        for module in (audit_homepages, apply_homepages):
            for repositories in (
                [],
                [{"name": "example/project"}],
                [{"name": "example/project", "homepage": ""}],
                [{"name": "example/project", "homepage": None, "protectd": True}],
            ):
                with self.subTest(module=module.__name__, repositories=repositories):
                    arguments = ["--apply"] if module is apply_homepages else []
                    result, calls, _ = self.run_cli(module, repositories, arguments, [])
                    self.assertEqual(result, 2)
                    self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
