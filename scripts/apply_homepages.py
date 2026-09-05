#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path

from github_auth import GITHUB_HOST, gh_environment
from homepage_common import RepositoryState, display, get_repository, identity_errors
from repository_config import REPOSITORY_NAME, ConfigError, RepositoryConfig, load_repositories


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "repositories.json"
UPDATE_HOMEPAGE = """
mutation($input: UpdateRepositoryInput!) {
  updateRepository(input: $input) {
    repository { id nameWithOwner homepageUrl }
  }
}
"""


def set_homepage(
    node_id: str, homepage: str | None, use_stored_gh_auth: bool
) -> None:
    env = gh_environment(use_stored_gh_auth)
    expected = "" if homepage is None else homepage

    result = subprocess.run(
        [
            "gh", "api", "graphql", "--hostname", GITHUB_HOST,
            "--method", "POST", "--input", "-",
        ],
        input=json.dumps({
            "query": UPDATE_HOMEPAGE,
            "variables": {"input": {"repositoryId": node_id, "homepageUrl": expected}},
        }),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    raw = json.loads(result.stdout)
    if not isinstance(raw, dict) or raw.get("errors"):
        raise ValueError("invalid GitHub mutation response or GraphQL errors")
    try:
        updated = raw["data"]["updateRepository"]["repository"]
    except (KeyError, TypeError):
        raise ValueError("invalid GitHub mutation response: missing repository") from None
    if (
        not isinstance(updated, dict)
        or updated.get("id") != node_id
        or not isinstance(updated.get("nameWithOwner"), str)
        or REPOSITORY_NAME.fullmatch(updated["nameWithOwner"]) is None
        or "homepageUrl" not in updated
        or not isinstance(updated["homepageUrl"], (str, type(None)))
        or (updated["homepageUrl"] or "") != expected
    ):
        raise ValueError("unexpected GitHub mutation response: repository ID or homepage")
    # Names may change after preflight; only the captured immutable ID must match.


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply expected GitHub repository homepage values."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify GitHub repositories. Without this flag, only show changes.",
    )
    parser.add_argument(
        "--use-stored-gh-auth",
        action="store_true",
        help="Ignore token environment variables and use the stored gh login.",
    )
    args = parser.parse_args()

    try:
        repositories = load_repositories(CONFIG_PATH)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    repository_states: dict[str, RepositoryState] = {}
    preflight_errors: list[str] = []
    canonical_names: dict[str, str] = {}

    for repository in repositories:
        try:
            current = get_repository(
                repository.name, args.use_stored_gh_auth
            )
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            print(f"{repository.name}")
            print("  Status: ERROR")
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"  {(detail or str(exc)).strip()}")
            print()
            preflight_errors.append(repository.name)
            continue

        repository_states[repository.name] = current
        errors = identity_errors(repository.name, current.full_name, canonical_names)
        if errors:
            for error in errors:
                print(error)
            preflight_errors.append(repository.name)

    if preflight_errors:
        print("Preflight failed; no repositories were modified.")
        print(f"Failed preflight: {', '.join(preflight_errors)}")
        return 1

    changes: list[RepositoryConfig] = []
    protected_drift = False

    for repository in repositories:
        name = repository.name
        expected = repository.homepage
        protected = repository.protected
        current = repository_states[name].homepage

        if current == expected:
            print(f"{name}: OK")
            continue

        if protected:
            print(f"{name}: REFUSED (protected)")
            print(f"  Current:  {display(current)}")
            print(f"  Expected: {display(expected)}")
            print()
            protected_drift = True
            continue

        print(f"{name}: WOULD CHANGE")
        print(f"  Current:  {display(current)}")
        print(f"  Expected: {display(expected)}")

        print()
        changes.append(repository)

    if protected_drift:
        print("Protected drift detected; no repositories were modified.")
        return 1

    if not args.apply and changes:
        print("Dry run only. Re-run with --apply to make these changes.")

    if not args.apply:
        return 0

    updated: list[str] = []
    for index, repository in enumerate(changes):
        try:
            set_homepage(
                repository_states[repository.name].node_id,
                repository.homepage,
                args.use_stored_gh_auth,
            )
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            print(f"{repository.name}: ERROR")
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"  {(detail or str(exc)).strip()}")
            print("Apply failed after preflight.")
            print(f"Updated: {', '.join(updated) if updated else '(none)'}")
            print(f"Failed: {repository.name}")
            pending = [entry.name for entry in changes[index + 1 :]]
            print(f"Pending (not attempted): {', '.join(pending) if pending else '(none)'}")
            return 1
        updated.append(repository.name)
        print(f"{repository.name}: UPDATED")

    return 0


if __name__ == "__main__":
    sys.exit(main())
