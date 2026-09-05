"""Shared read-only homepage lookup and display helpers."""

from dataclasses import dataclass
import json
import re
import subprocess

from github_auth import GITHUB_HOST, gh_environment
from repository_config import REPOSITORY_NAME


# Accept both legacy and newer opaque GitHub node IDs without decoding them.
NODE_ID = re.compile(r"[A-Za-z0-9_+/=-]+")


@dataclass(frozen=True)
class RepositoryState:
    full_name: str
    homepage: str | None
    node_id: str


def get_repository(repository: str, use_stored_gh_auth: bool) -> RepositoryState:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}", "--hostname", GITHUB_HOST],
        check=True,
        capture_output=True,
        text=True,
        env=gh_environment(use_stored_gh_auth),
    )
    raw = json.loads(result.stdout)
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("full_name"), str)
        or REPOSITORY_NAME.fullmatch(raw["full_name"]) is None
        or "homepage" not in raw
        or not isinstance(raw["homepage"], (str, type(None)))
        or not isinstance(raw.get("node_id"), str)
        or NODE_ID.fullmatch(raw["node_id"]) is None
    ):
        raise ValueError(
            "invalid GitHub response: expected repository full_name, homepage, and node_id"
        )
    return RepositoryState(raw["full_name"], raw["homepage"] or None, raw["node_id"])


def identity_errors(
    configured_name: str, full_name: str, seen: dict[str, str]
) -> list[str]:
    """Check identity and record the first configured name for each canonical name."""
    errors = []
    canonical_name = full_name.casefold()
    if canonical_name != configured_name.casefold():
        errors.append(
            f"Identity mismatch (redirect/alias): configured {configured_name}, "
            f"resolved canonical name {full_name}"
        )
    if canonical_name in seen:
        errors.append(
            f"Canonical collision: configured {seen[canonical_name]} and "
            f"{configured_name} both resolve to {full_name}"
        )
    else:
        seen[canonical_name] = configured_name
    return errors


def display(value: str | None) -> str:
    return value if value is not None else "(none)"
