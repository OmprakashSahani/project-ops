#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

from homepage_common import display, get_repository, identity_errors
from repository_config import ConfigError, load_repositories


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "repositories.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GitHub repository homepages.")
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

    has_drift = False
    canonical_names: dict[str, str] = {}

    for repository in repositories:
        name = repository.name
        expected = repository.homepage
        protected = repository.protected

        try:
            resolved = get_repository(name, args.use_stored_gh_auth)
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            print(f"{name}")
            print("  Status: ERROR")
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"  {(detail or str(exc)).strip()}")
            print()
            has_drift = True
            continue

        current = resolved.homepage
        errors = identity_errors(name, resolved.full_name, canonical_names)
        matches = current == expected
        if errors:
            status = "ERROR (repository identity)"
        elif matches:
            status = "OK (protected)" if protected else "OK"
        elif protected:
            status = "REFUSED (protected)"
        else:
            status = "STALE"

        print(name)
        print(f"  Canonical: {resolved.full_name}")
        print(f"  Current:  {display(current)}")
        print(f"  Expected: {display(expected)}")
        print(f"  Status:   {status}")
        for error in errors:
            print(f"  {error}")
        print()

        if errors or not matches:
            has_drift = True

    return 1 if has_drift else 0


if __name__ == "__main__":
    sys.exit(main())
