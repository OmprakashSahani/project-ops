"""Shared read-only homepage lookup and display helpers."""

import subprocess

from github_auth import gh_environment


def get_homepage(repository: str, use_stored_gh_auth: bool) -> str | None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}", "--jq", ".homepage // empty"],
        check=True,
        capture_output=True,
        text=True,
        env=gh_environment(use_stored_gh_auth),
    )
    homepage = result.stdout.strip()
    return homepage or None


def display(value: str | None) -> str:
    return value if value is not None else "(none)"
