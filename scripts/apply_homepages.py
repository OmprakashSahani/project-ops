#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

from github_auth import gh_environment
from homepage_common import display, get_homepage
from repository_config import ConfigError, RepositoryConfig, load_repositories


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "repositories.json"


def set_homepage(
    repository: str, homepage: str | None, use_stored_gh_auth: bool
) -> None:
    env = gh_environment(use_stored_gh_auth)

    subprocess.run(
        [
            "gh",
            "repo",
            "edit",
            repository,
            "--homepage",
            "" if homepage is None else homepage,
        ],
        check=True,
        env=env,
    )


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

    current_homepages: dict[str, str | None] = {}
    preflight_errors: list[str] = []

    for repository in repositories:
        try:
            current_homepages[repository.name] = get_homepage(
                repository.name, args.use_stored_gh_auth
            )
        except subprocess.CalledProcessError as exc:
            print(f"{repository.name}")
            print("  Status: ERROR")
            print(f"  {exc.stderr.strip()}")
            print()
            preflight_errors.append(repository.name)

    if preflight_errors:
        print("Preflight failed; no repositories were modified.")
        print(f"Failed to read: {', '.join(preflight_errors)}")
        return 1

    changes: list[RepositoryConfig] = []
    protected_drift = False

    for repository in repositories:
        name = repository.name
        expected = repository.homepage
        protected = repository.protected
        current = current_homepages[name]

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
                repository.name,
                repository.homepage,
                args.use_stored_gh_auth,
            )
        except subprocess.CalledProcessError:
            print(f"{repository.name}: ERROR")
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
