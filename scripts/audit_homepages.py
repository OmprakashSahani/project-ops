#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

from homepage_common import display, get_homepage
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

    for repository in repositories:
        name = repository.name
        expected = repository.homepage
        protected = repository.protected

        try:
            current = get_homepage(name, args.use_stored_gh_auth)
        except subprocess.CalledProcessError as exc:
            print(f"{name}")
            print("  Status: ERROR")
            print(f"  {exc.stderr.strip()}")
            print()
            has_drift = True
            continue

        matches = current == expected
        if matches:
            status = "OK (protected)" if protected else "OK"
        elif protected:
            status = "REFUSED (protected)"
        else:
            status = "STALE"

        print(name)
        print(f"  Current:  {display(current)}")
        print(f"  Expected: {display(expected)}")
        print(f"  Status:   {status}")
        print()

        if not matches:
            has_drift = True

    return 1 if has_drift else 0


if __name__ == "__main__":
    sys.exit(main())
