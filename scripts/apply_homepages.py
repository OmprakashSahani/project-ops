#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "repositories.json"


def get_homepage(repository: str) -> str | None:
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)

    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}",
            "--jq",
            ".homepage // empty",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    homepage = result.stdout.strip()
    return homepage or None


def set_homepage(repository: str, homepage: str | None) -> None:
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)

    subprocess.run(
        [
            "gh",
            "repo",
            "edit",
            repository,
            "--homepage",
            homepage or "",
        ],
        check=True,
        env=env,
    )


def display(value: str | None) -> str:
    return value if value is not None else "(none)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply expected GitHub repository homepage values."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify GitHub repositories. Without this flag, only show changes.",
    )
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text())
    changes = 0

    for repository in config["repositories"]:
        name = repository["name"]
        expected = repository.get("homepage")
        protected = repository.get("protected", False)

        try:
            current = get_homepage(name)
        except subprocess.CalledProcessError as exc:
            print(f"{name}")
            print("  Status: ERROR")
            print(f"  {exc.stderr.strip()}")
            print()
            return 1

        if current == expected:
            print(f"{name}: OK")
            continue

        if protected:
            print(f"{name}: REFUSED (protected)")
            print(f"  Current:  {display(current)}")
            print(f"  Expected: {display(expected)}")
            print()
            continue

        print(f"{name}: {'APPLY' if args.apply else 'WOULD CHANGE'}")
        print(f"  Current:  {display(current)}")
        print(f"  Expected: {display(expected)}")

        if args.apply:
            try:
                set_homepage(name, expected)
            except subprocess.CalledProcessError:
                print("  Result: ERROR")
                return 1
            print("  Result: UPDATED")

        print()
        changes += 1

    if not args.apply and changes:
        print("Dry run only. Re-run with --apply to make these changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
