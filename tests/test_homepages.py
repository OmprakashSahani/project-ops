from contextlib import redirect_stderr, redirect_stdout
import base64
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
from homepage_common import RepositoryState  # noqa: E402


GRAPHQL_COMMAND = [
    "gh", "api", "graphql", "--hostname", "github.com",
    "--method", "POST", "--input", "-",
]


def repository_id(full_name):
    return base64.b64encode(full_name.casefold().encode()).decode()


def api_response(full_name, homepage, node_id=None):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({
            "full_name": full_name, "homepage": homepage,
            "node_id": repository_id(full_name) if node_id is None else node_id,
        })
    )


def mutation_response(full_name, homepage=None, node_id=None):
    return subprocess.CompletedProcess([], 0, json.dumps({
        "data": {"updateRepository": {"repository": {
            "id": repository_id(full_name) if node_id is None else node_id,
            "nameWithOwner": full_name, "homepageUrl": homepage,
        }}}
    }))


def mutation_input(subprocess_call):
    return json.loads(subprocess_call.kwargs["input"])["variables"]["input"]


# Raw JSON is essential: dictionaries discard duplicate keys before serialization.
DUPLICATE_ROOT_JSON = (
    '{"repositories": [{"name": "example/protected", "homepage": null, '
    '"protected": true}], "repositories": '
    '[{"name": "example/project", "homepage": null}]}'
)
DUPLICATE_ENTRY_JSON = {
    "protected": '{"name":"example/project","homepage":null,'
                 '"protected":true,"protected":false}',
    "homepage": '{"name":"example/project","homepage":"https://keep.example",'
                '"homepage":null}',
    "name": '{"name":"example/protected","name":"example/project","homepage":null}',
}


class ConfigTestCase(unittest.TestCase):
    def load(self, repositories):
        return self.load_raw(json.dumps({"repositories": repositories}))

    def load_raw(self, raw_json):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repositories.json"
            path.write_text(raw_json, encoding="utf-8")
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

    def test_duplicate_root_key_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "duplicate JSON key: 'repositories'"):
            self.load_raw(DUPLICATE_ROOT_JSON)

    def test_duplicate_repository_entry_keys_are_rejected(self):
        for key, entry in DUPLICATE_ENTRY_JSON.items():
            with self.subTest(key=key):
                with self.assertRaisesRegex(ConfigError, f"duplicate JSON key: '{key}'"):
                    self.load_raw('{"repositories":[' + entry + ']}')

    def test_duplicate_keys_in_nested_objects_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, "duplicate JSON key: 'nested'"):
            self.load_raw('{"repositories":[{"nested":{"nested":1,"nested":2}}]}')

    def test_unknown_top_level_fields_are_rejected(self):
        for field in ("protected", "homepage", "name", "unexpected"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ConfigError, f"top-level has unknown fields: {field}"):
                    self.load_raw(json.dumps({
                        "repositories": [{"name": "example/project", "homepage": None}],
                        field: True,
                    }))

    def test_nul_homepage_is_rejected(self):
        self.assert_invalid(
            [{"name": "example/project", "homepage": "https://example.com\x00"}],
            "must not contain control characters",
        )

    def test_other_controls_and_unpaired_surrogates_are_rejected(self):
        for character in ("\t", "\r", "\n", "\x1b", "\x7f", "\x85", "\ud800"):
            with self.subTest(character=repr(character)):
                self.assert_invalid(
                    [{"name": "example/project", "homepage": "https://example.com/" + character}],
                    "must not contain control characters or unpaired surrogates",
                )

    def test_valid_url_characters_are_preserved(self):
        for homepage in (
            "https://example.com/path?q=one%20two&next=%00#section",
            "https://example.com/café",
        ):
            with self.subTest(homepage=homepage):
                repositories = self.load([{"name": "example/project", "homepage": homepage}])
                self.assertEqual(repositories[0].homepage, homepage)

    def test_non_utf8_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repositories.json"
            path.write_bytes(b'\xff')
            with self.assertRaisesRegex(ConfigError, "configuration must be UTF-8"):
                load_repositories(path)

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

    def test_get_repository_pins_github_com_despite_ambient_host(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin",
            "GH_HOST": "enterprise.example", "GH_ENTERPRISE_TOKEN": "enterprise",
            "GITHUB_ENTERPRISE_TOKEN": "enterprise-fallback",
        }
        for module in (audit_homepages, apply_homepages):
            for stored_auth in (False, True):
                with self.subTest(module=module.__name__, stored_auth=stored_auth):
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch("subprocess.run") as run,
                    ):
                        run.return_value.stdout = api_response("example/project", "https://example.com").stdout
                        result = module.get_repository("example/project", stored_auth)

                        expected_env = {"PATH": "/bin", "GH_HOST": "github.com"}
                        if not stored_auth:
                            expected_env.update(GITHUB_TOKEN="integration", GH_TOKEN="explicit")
                        run.assert_called_once_with(
                            ["gh", "api", "repos/example/project", "--hostname", "github.com"],
                            check=True, capture_output=True, text=True, env=expected_env,
                        )
                        self.assertEqual(result, RepositoryState("example/project", "https://example.com", repository_id("example/project")))
                        self.assertEqual(dict(os.environ), environment)

    def test_set_homepage_pins_github_com_and_passes_explicit_null(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin",
            "GH_HOST": "enterprise.example", "GH_ENTERPRISE_TOKEN": "enterprise",
            "GITHUB_ENTERPRISE_TOKEN": "enterprise-fallback",
        }
        for stored_auth in (False, True):
            for homepage in (None, "https://example.com", 'https://example.com/?q="x"&next={repo}'):
                with self.subTest(stored_auth=stored_auth, homepage=homepage):
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch("subprocess.run") as run,
                    ):
                        run.return_value = mutation_response("example/project", homepage)
                        apply_homepages.set_homepage(
                            repository_id("example/project"), homepage, stored_auth
                        )
                        expected_env = {"PATH": "/bin", "GH_HOST": "github.com"}
                        if not stored_auth:
                            expected_env.update(GITHUB_TOKEN="integration", GH_TOKEN="explicit")
                        run.assert_called_once()
                        self.assertEqual(run.call_args.args, (GRAPHQL_COMMAND,))
                        self.assertEqual(
                            {k: v for k, v in run.call_args.kwargs.items() if k != "input"},
                            {"check": True, "capture_output": True, "text": True, "env": expected_env},
                        )
                        self.assertEqual(mutation_input(run.call_args), {
                            "repositoryId": repository_id("example/project"),
                            "homepageUrl": "" if homepage is None else homepage,
                        })
                        payload = json.loads(run.call_args.kwargs["input"])
                        self.assertEqual(
                            " ".join(payload["query"].split()),
                            "mutation($input: UpdateRepositoryInput!) { updateRepository(input: $input) "
                            "{ repository { id nameWithOwner homepageUrl } } }",
                        )
                        self.assertEqual(dict(os.environ), environment)

    def test_repository_state_preserves_opaque_node_id_from_same_read(self):
        for node_id in ("MDEwOlJlcG9zaXRvcnkxMjM=", "R_kgDOExample", "CLEANUP_ID"):
            with self.subTest(node_id=node_id):
                with patch("subprocess.run", return_value=api_response(
                    "example/project", None, node_id
                )) as run:
                    state = apply_homepages.get_repository("example/project", False)
                self.assertEqual(state, RepositoryState("example/project", None, node_id))
                run.assert_called_once()


class ApplySafetyTestCase(unittest.TestCase):
    def run_apply(self, repositories, *arguments, get_repository, write_effect=None):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps({"repositories": repositories}))
            with (
                patch.object(apply_homepages, "CONFIG_PATH", config_path),
                patch.object(
                    apply_homepages, "get_repository", side_effect=[
                        value if isinstance(value, Exception) else RepositoryState(entry["name"], value, repository_id(entry["name"]))
                        for entry, value in zip(repositories, get_repository)
                    ]
                ) as get_repository_mock,
                patch.object(
                    apply_homepages, "set_homepage", side_effect=write_effect
                ) as set_homepage,
                patch.object(sys, "argv", ["apply_homepages.py", *arguments]),
            ):
                with redirect_stdout(io.StringIO()) as output:
                    result = apply_homepages.main()
                self.output = output.getvalue()
                self.reads = get_repository_mock.call_args_list
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
            get_repository=["https://current.example"],
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
            get_repository=["https://stale.example", failure, None],
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
                    get_repository=["https://stale.example"] * 3,
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
            get_repository=["https://stale.example"] * 4,
            write_effect=[None, failure],
        )

        self.assertNotEqual(result, 0)
        self.assertEqual(set_homepage.call_args_list, [
            call(repository_id("example/first"), None, False),
            call(repository_id("example/second"), None, False),
        ])
        self.assertIn("Updated: example/first\n", self.output)
        self.assertIn("Failed: example/second\n", self.output)
        self.assertIn(
            "Pending (not attempted): example/third, example/fourth\n", self.output
        )

    def test_dry_run_results_in_zero_writes(self):
        result, set_homepage = self.run_apply(
            [{"name": "example/project", "homepage": None}],
            get_repository=["https://stale.example"],
        )

        self.assertEqual(result, 0)
        set_homepage.assert_not_called()


class CliIntegrationTestCase(unittest.TestCase):
    """Exercise real CLI parsing, config loading, and helpers with gh mocked."""

    def run_cli(self, module, repositories, arguments, responses, *, raw_json=None):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                raw_json if raw_json is not None else json.dumps({"repositories": repositories}),
                encoding="utf-8",
            )
            with (
                patch.object(module, "CONFIG_PATH", config_path),
                patch.object(sys, "argv", [module.__file__, *arguments]),
                patch("subprocess.run", side_effect=responses) as run,
                redirect_stdout(io.StringIO()) as output,
                redirect_stderr(io.StringIO()) as errors,
            ):
                result = module.main()
            self.errors = errors.getvalue()
        return result, run.call_args_list, output.getvalue()

    def test_auth_modes_work_through_both_cli_main_functions(self):
        environment = {
            "GITHUB_TOKEN": "integration", "GH_TOKEN": "explicit", "PATH": "/bin",
            "GH_HOST": "enterprise.example", "GH_ENTERPRISE_TOKEN": "enterprise",
            "GITHUB_ENTERPRISE_TOKEN": "enterprise-fallback",
        }
        for module in (audit_homepages, apply_homepages):
            for stored_auth in (False, True):
                with self.subTest(module=module.__name__, stored_auth=stored_auth):
                    arguments = ["--use-stored-gh-auth"] if stored_auth else []
                    responses = [api_response("example/project", "https://old.example")]
                    expected_commands = [
                        ["gh", "api", "repos/example/project", "--hostname", "github.com"]
                    ]
                    if module is apply_homepages:
                        arguments.append("--apply")
                        responses.append(mutation_response("example/project"))
                        expected_commands.append(
                            GRAPHQL_COMMAND
                        )
                    with patch.dict(os.environ, environment, clear=True):
                        result, calls, _ = self.run_cli(
                            module, [{"name": "example/project", "homepage": None}],
                            arguments, responses,
                        )
                        self.assertEqual(dict(os.environ), environment)
                    self.assertEqual(result, 0 if module is apply_homepages else 1)
                    self.assertEqual([c.args[0] for c in calls], expected_commands)
                    expected_env = {"PATH": "/bin", "GH_HOST": "github.com"}
                    if not stored_auth:
                        expected_env.update(GITHUB_TOKEN="integration", GH_TOKEN="explicit")
                    for subprocess_call in calls:
                        self.assertEqual(subprocess_call.kwargs["env"], expected_env)

    def test_matching_identity_allows_normal_audit_and_apply(self):
        for module in (audit_homepages, apply_homepages):
            with self.subTest(module=module.__name__):
                arguments = ["--apply"] if module is apply_homepages else []
                responses = [api_response("example/project", "https://old.example")]
                if module is apply_homepages:
                    responses.append(mutation_response("example/project"))
                result, calls, output = self.run_cli(
                    module, [{"name": "example/project", "homepage": None}],
                    arguments, responses,
                )
                self.assertEqual(result, 0 if module is apply_homepages else 1)
                self.assertEqual(len(calls), 2 if module is apply_homepages else 1)
                self.assertIn("UPDATED" if module is apply_homepages else "STALE", output)
                self.assertNotIn("Identity mismatch", output)

    def test_case_only_identity_difference_is_allowed(self):
        for module in (audit_homepages, apply_homepages):
            with self.subTest(module=module.__name__):
                arguments = ["--apply"] if module is apply_homepages else []
                current = "https://old.example" if module is apply_homepages else None
                responses = [api_response("Example/PROJECT", current)]
                if module is apply_homepages:
                    responses.append(mutation_response("example/project"))
                result, calls, output = self.run_cli(
                    module, [{"name": "example/project", "homepage": None}],
                    arguments, responses,
                )
                self.assertEqual(result, 0)
                self.assertEqual(len(calls), 2 if module is apply_homepages else 1)
                self.assertNotIn("Identity mismatch", output)

    def test_audit_reports_alias_even_when_homepage_matches(self):
        result, calls, output = self.run_cli(
            audit_homepages,
            [{"name": "example/alias", "homepage": None},
             {"name": "example/last", "homepage": None}], [],
            [api_response("example/canonical", None), api_response("example/last", None)],
        )
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
        self.assertIn("Identity mismatch", output)
        self.assertIn("configured example/alias", output)
        self.assertIn("resolved canonical name example/canonical", output)

    def test_apply_alias_blocks_all_writes_after_all_reads(self):
        for arguments in ([], ["--apply"]):
            with self.subTest(arguments=arguments):
                result, calls, output = self.run_cli(
                    apply_homepages,
                    [{"name": f"example/{name}", "homepage": None}
                     for name in ("first", "alias", "last")], arguments,
                    [api_response("example/first", "https://old.example"),
                     api_response("example/canonical", None),
                     api_response("example/last", "https://old.example")],
                )
                self.assertEqual(result, 1)
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
                self.assertIn("configured example/alias", output)
                self.assertIn("resolved canonical name example/canonical", output)
                self.assertIn("no repositories were modified", output)

    def test_unprotected_alias_to_krsna_blocks_all_writes_in_either_order(self):
        krsna = "OmprakashSahani/Krsna"
        homepage = "https://krsna-supreme-personality-of-godhead.vercel.app/"
        entries = [
            {"name": "OmprakashSahani/Krsna-old", "homepage": None},
            {"name": krsna, "homepage": homepage, "protected": True},
        ]
        for repositories in (entries, entries[::-1]):
            with self.subTest(first=repositories[0]["name"]):
                result, calls, output = self.run_cli(
                    apply_homepages, repositories, ["--apply"],
                    [api_response(krsna, homepage)] * 2,
                )
                self.assertEqual(result, 1)
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
                self.assertIn("Identity mismatch", output)
                self.assertIn("Canonical collision", output)
                self.assertIn("OmprakashSahani/Krsna-old", output)
                self.assertIn(krsna, output)
                self.assertIn("no repositories were modified", output)

    def test_canonical_collisions_are_case_insensitive_in_both_commands(self):
        for module in (audit_homepages, apply_homepages):
            with self.subTest(module=module.__name__):
                result, calls, output = self.run_cli(
                    module,
                    [{"name": f"example/{name}", "homepage": None}
                     for name in ("first", "second", "last")],
                    ["--apply"] if module is apply_homepages else [],
                    [api_response("example/canonical", "https://old.example"),
                     api_response("EXAMPLE/CANONICAL", "https://old.example"),
                     api_response("example/last", None)],
                )
                self.assertEqual(result, 1)
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
                self.assertIn(
                    "Canonical collision: configured example/first and example/second "
                    "both resolve to EXAMPLE/CANONICAL", output,
                )

    def test_invalid_repository_response_fails_closed_after_all_reads(self):
        for payload in (
            "not JSON", "null", "[]", "{}",
            '{"full_name":null,"homepage":null,"node_id":"PROJECT_ID"}',
            '{"full_name":"","homepage":null,"node_id":"PROJECT_ID"}',
            '{"full_name":"example/project","node_id":"PROJECT_ID"}',
            '{"full_name":"example/project","homepage":false,"node_id":"PROJECT_ID"}',
        ):
            for module in (audit_homepages, apply_homepages):
                with self.subTest(payload=payload, module=module.__name__):
                    result, calls, output = self.run_cli(
                        module,
                        [{"name": f"example/{name}", "homepage": None}
                         for name in ("first", "project", "last")],
                        ["--apply"] if module is apply_homepages else [],
                        [api_response("example/first", "https://old.example"),
                         subprocess.CompletedProcess([], 0, payload),
                         api_response("example/last", None)],
                    )
                    self.assertEqual(result, 1)
                    self.assertEqual(len(calls), 3)
                    self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
                    self.assertIn("ERROR", output)

    def test_apply_completes_all_reads_before_any_write(self):
        result, calls, _ = self.run_cli(
            apply_homepages,
            [{"name": f"example/{name}", "homepage": None}
             for name in ("first", "second", "third")],
            ["--apply"],
            [api_response(f"example/{name}", "https://old.example")
             for name in ("first", "second", "third")]
            + [mutation_response(f"example/{name}") for name in ("first", "second", "third")],
        )
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", f"repos/example/{name}", "--hostname", "github.com"]
            for name in ("first", "second", "third")
        ] + [
            GRAPHQL_COMMAND for _ in range(3)
        ])
        self.assertEqual([mutation_input(c) for c in calls[3:]], [
            {"repositoryId": repository_id(f"example/{name}"), "homepageUrl": ""}
            for name in ("first", "second", "third")
        ])

    def test_dry_run_only_invokes_read_commands(self):
        result, calls, output = self.run_cli(
            apply_homepages, [{"name": "example/project", "homepage": None}], [],
            [api_response("example/project", "https://old.example")],
        )
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", "repos/example/project", "--hostname", "github.com"]
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
                api_response("example/ordinary", "https://old.example"),
                api_response("example/protected", "https://old.example"),
                subprocess.CalledProcessError(1, ["gh"], stderr="simulated failure"),
                api_response("example/matching", None),
            ],
        )
        self.assertNotEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ["gh", "api", f"repos/example/{name}", "--hostname", "github.com"]
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
                [
                    {"name": "example/first", "homepage": None},
                    {"name": "example/second", "homepage": "https://example.com\x00"},
                ],
            ):
                with self.subTest(module=module.__name__, repositories=repositories):
                    arguments = ["--apply"] if module is apply_homepages else []
                    result, calls, _ = self.run_cli(module, repositories, arguments, [])
                    self.assertEqual(result, 2)
                    self.assertEqual(calls, [])

    def test_raw_config_errors_are_concise_and_never_invoke_gh(self):
        cases = [(DUPLICATE_ROOT_JSON, "duplicate JSON key: 'repositories'")]
        cases.extend(
            ('{"repositories":[' + entry + ']}', f"duplicate JSON key: '{key}'")
            for key, entry in DUPLICATE_ENTRY_JSON.items()
        )
        cases.append((
            '{"repositories":[{"name":"example/project","homepage":null}],"protected":true}',
            "top-level has unknown fields: protected",
        ))
        for module in (audit_homepages, apply_homepages):
            for raw_json, message in cases:
                with self.subTest(module=module.__name__, message=message):
                    arguments = ["--apply"] if module is apply_homepages else []
                    result, calls, _ = self.run_cli(
                        module, None, arguments, [], raw_json=raw_json,
                    )
                    self.assertEqual(result, 2)
                    self.assertEqual(calls, [])
                    self.assertEqual(self.errors, f"Configuration error: {message}\n")

    def test_runtime_write_failures_report_updated_failed_and_pending(self):
        names = [f"example/{name}" for name in ("first", "second", "third")]
        for failure in (
            subprocess.CalledProcessError(1, ["gh", "repo", "edit"]),
            OSError("could not start gh"),
            ValueError("invalid subprocess environment"),
        ):
            for index in range(len(names)):
                with self.subTest(failure=type(failure).__name__, index=index):
                    result, calls, output = self.run_cli(
                        apply_homepages,
                        [{"name": name, "homepage": None} for name in names],
                        ["--apply"],
                        [api_response(name, "https://old.example") for name in names]
                        + [mutation_response(name) for name in names[:index]] + [failure],
                    )
                    self.assertEqual(result, 1)
                    self.assertEqual(len(calls), 3 + index + 1)
                    self.assertIn(f"Updated: {', '.join(names[:index]) or '(none)'}\n", output)
                    self.assertIn(f"Failed: {names[index]}\n", output)
                    self.assertIn(
                        f"Pending (not attempted): {', '.join(names[index + 1:]) or '(none)'}\n",
                        output,
                    )

    def test_matching_krsna_is_skipped_while_other_repositories_are_updated(self):
        raw_json = (SCRIPTS.parent / "config/repositories.json").read_text()
        result, calls, _ = self.run_cli(
            apply_homepages, None, ["--apply"],
            [api_response(entry["name"], "https://old.example")
             for entry in json.loads(raw_json)["repositories"][:2]]
            + [api_response(
                "OmprakashSahani/Krsna",
                "https://krsna-supreme-personality-of-godhead.vercel.app/"
            )]
            + [mutation_response(entry["name"]) for entry in json.loads(raw_json)["repositories"][:2]],
            raw_json=raw_json,
        )
        self.assertEqual(result, 0)
        self.assertEqual([
            mutation_input(c)["repositoryId"] for c in calls if c.args[0][2] == "graphql"
        ], [
            repository_id("OmprakashSahani/lerobot-state-atlas"),
            repository_id("OmprakashSahani/codex-benchmark-guardian"),
        ])

    def test_missing_or_invalid_node_id_blocks_writes_after_all_reads(self):
        missing = object()
        for node_id in (missing, None, False, 123, [], {}, "", " ", "A B", "ID\n", "ID\x00", "é", "\ud800"):
            for module in (audit_homepages, apply_homepages):
                with self.subTest(node_id=repr(node_id), module=module.__name__):
                    payload = {"full_name": "example/project", "homepage": None}
                    if node_id is not missing:
                        payload["node_id"] = node_id
                    result, calls, output = self.run_cli(
                        module,
                        [{"name": f"example/{name}", "homepage": None}
                         for name in ("first", "project", "last")],
                        ["--apply"] if module is apply_homepages else [],
                        [api_response("example/first", "https://old.example"),
                         subprocess.CompletedProcess([], 0, json.dumps(payload)),
                         api_response("example/last", None)],
                    )
                    self.assertEqual(result, 1)
                    self.assertEqual(len(calls), 3)
                    self.assertTrue(all(c.args[0][2].startswith("repos/") for c in calls))
                    self.assertIn("node_id", output)

    def test_rename_after_preflight_updates_captured_id(self):
        names = {"example/cleanup": "CLEANUP_ID"}
        mutations = []

        def gh(command, **kwargs):
            if command[2].startswith("repos/"):
                self.assertEqual(command[2], "repos/example/cleanup")
                node_id = names.pop("example/cleanup")
                names["example/renamed"] = node_id
                return api_response("example/cleanup", "https://old.example", node_id)
            self.assertEqual(command, GRAPHQL_COMMAND)
            mutation = json.loads(kwargs["input"])["variables"]["input"]
            mutations.append(mutation)
            self.assertEqual(mutation["repositoryId"], names["example/renamed"])
            return mutation_response("example/renamed", "", mutation["repositoryId"])

        result, calls, output = self.run_cli(
            apply_homepages, [{"name": "example/cleanup", "homepage": None}],
            ["--apply"], gh,
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(mutations, [{"repositoryId": "CLEANUP_ID", "homepageUrl": ""}])
        self.assertIn("UPDATED", output)

    def test_name_reuse_by_krsna_never_changes_mutation_target(self):
        cleanup = "OmprakashSahani/lerobot-state-atlas"
        krsna = "OmprakashSahani/Krsna"
        keep = "https://krsna-supreme-personality-of-godhead.vercel.app/"
        names = {cleanup: "CLEANUP_ID", krsna: "KRSNA_ID"}
        homepages = {"CLEANUP_ID": "https://old.example", "KRSNA_ID": keep}
        mutations = []

        def gh(command, **kwargs):
            if command[2].startswith("repos/"):
                name = command[2].removeprefix("repos/")
                node_id = names[name]
                response = api_response(name, homepages[node_id], node_id)
                if name == krsna:
                    # Change both names after their preflight snapshots were taken.
                    names[cleanup + "-moved"] = names.pop(cleanup)
                    names[cleanup] = names.pop(krsna)
                return response
            self.assertEqual(command, GRAPHQL_COMMAND)
            mutation = json.loads(kwargs["input"])["variables"]["input"]
            mutations.append(mutation)
            self.assertEqual(names[cleanup], "KRSNA_ID")
            self.assertEqual(mutation["repositoryId"], "CLEANUP_ID")
            homepages[mutation["repositoryId"]] = mutation["homepageUrl"]
            return mutation_response(cleanup + "-moved", "", "CLEANUP_ID")

        result, calls, _ = self.run_cli(
            apply_homepages,
            [{"name": cleanup, "homepage": None},
             {"name": krsna, "homepage": keep, "protected": True}],
            ["--apply"], gh,
        )
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0][2] for c in calls], [f"repos/{cleanup}", f"repos/{krsna}", "graphql"])
        self.assertEqual(mutations, [{"repositoryId": "CLEANUP_ID", "homepageUrl": ""}])
        self.assertEqual(homepages, {"CLEANUP_ID": "", "KRSNA_ID": keep})

    def test_invalid_mutation_responses_use_partial_failure_reporting(self):
        valid = json.loads(mutation_response("example/second").stdout)
        payloads = ["not JSON", "null", "[]", "{}"]
        payloads.extend(json.dumps(payload) for payload in (
            {"errors": [{"message": "permission denied"}], "data": valid["data"]},
            {"data": None}, {"data": []}, {"data": {"updateRepository": None}},
            {"data": {"updateRepository": {"repository": None}}},
        ))
        for key, value in (
            ("id", "KRSNA_ID"), ("id", None),
            ("nameWithOwner", None), ("nameWithOwner", "invalid"),
            ("homepageUrl", False), ("homepageUrl", "https://unexpected.example"),
        ):
            payload = json.loads(mutation_response("example/second").stdout)
            payload["data"]["updateRepository"]["repository"][key] = value
            payloads.append(json.dumps(payload))
        for key in ("id", "nameWithOwner", "homepageUrl"):
            payload = json.loads(mutation_response("example/second").stdout)
            del payload["data"]["updateRepository"]["repository"][key]
            payloads.append(json.dumps(payload))
        for payload in payloads:
            with self.subTest(payload=payload):
                names = [f"example/{name}" for name in ("first", "second", "third")]
                result, calls, output = self.run_cli(
                    apply_homepages, [{"name": name, "homepage": None} for name in names],
                    ["--apply"],
                    [api_response(name, "https://old.example") for name in names]
                    + [mutation_response("example/first"), subprocess.CompletedProcess([], 0, payload)],
                )
                self.assertEqual(result, 1)
                self.assertEqual(len(calls), 5)
                self.assertIn("Updated: example/first\n", output)
                self.assertIn("Failed: example/second\n", output)
                self.assertIn("Pending (not attempted): example/third\n", output)
                self.assertNotIn("example/second: UPDATED", output)

    def test_nonempty_homepage_mutation_requires_matching_returned_homepage(self):
        expected = "https://new.example"
        for returned in (None, "", "https://wrong.example", expected):
            with self.subTest(returned=returned):
                result, calls, output = self.run_cli(
                    apply_homepages, [{"name": "example/project", "homepage": expected}],
                    ["--apply"], [api_response("example/project", "https://old.example"),
                                  mutation_response("example/project", returned)],
                )
                self.assertEqual(result, 0 if returned == expected else 1)
                self.assertEqual(mutation_input(calls[-1]), {
                    "repositoryId": repository_id("example/project"), "homepageUrl": expected,
                })
                self.assertIn("UPDATED" if returned == expected else "Failed: example/project", output)


if __name__ == "__main__":
    unittest.main()
