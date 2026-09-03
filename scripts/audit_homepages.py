#!/usr/bin/env python3

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


def display(value: str | None) -> str:
    return value if value is not None else "(none)"


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text())
    has_drift = False

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
            has_drift = True
            continue

        matches = current == expected
        status = "OK" if matches else "STALE"
        if protected:
            status += " (protected)"

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
