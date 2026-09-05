"""Authentication environment handling for GitHub CLI subprocesses."""

import os


GITHUB_HOST = "github.com"


def gh_environment(use_stored_gh_auth: bool) -> dict[str, str]:
    env = os.environ.copy()
    # This tool manages github.com only, regardless of the caller's gh defaults.
    env["GH_HOST"] = GITHUB_HOST
    env.pop("GH_ENTERPRISE_TOKEN", None)
    env.pop("GITHUB_ENTERPRISE_TOKEN", None)
    if use_stored_gh_auth:
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
    return env
