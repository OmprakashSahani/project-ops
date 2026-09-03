"""Authentication environment handling for GitHub CLI subprocesses."""

import os


def gh_environment(use_stored_gh_auth: bool) -> dict[str, str]:
    env = os.environ.copy()
    if use_stored_gh_auth:
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
    return env
