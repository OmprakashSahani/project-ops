"""Loading and validation for repository homepage configuration."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


REPOSITORY_NAME = re.compile(
    r"(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9-]{0,37}[A-Za-z0-9])"
    r"/[A-Za-z0-9._-]{1,100}"
)


class ConfigError(ValueError):
    """Raised when the repository configuration is invalid."""


@dataclass(frozen=True)
class RepositoryConfig:
    name: str
    homepage: str | None
    protected: bool = False


def load_repositories(path: Path) -> list[RepositoryConfig]:
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("repositories"), list):
        raise ConfigError('top-level "repositories" must be a list')
    if not raw["repositories"]:
        raise ConfigError('"repositories" must contain at least one repository')

    repositories: list[RepositoryConfig] = []
    seen: set[str] = set()

    for index, entry in enumerate(raw["repositories"], start=1):
        label = f"repository entry {index}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{label} must be an object")
        unknown_fields = entry.keys() - {"name", "homepage", "protected"}
        if unknown_fields:
            raise ConfigError(
                f"{label} has unknown fields: {', '.join(sorted(unknown_fields))}"
            )
        if "name" not in entry:
            raise ConfigError(f'{label} is missing required field "name"')
        if "homepage" not in entry:
            raise ConfigError(f'{label} is missing required field "homepage"')

        name = entry["name"]
        homepage = entry["homepage"]
        protected = entry.get("protected", False)

        if (
            not isinstance(name, str)
            or REPOSITORY_NAME.fullmatch(name) is None
            or name.split("/", 1)[1] in {".", ".."}
        ):
            raise ConfigError(f'{label} has invalid repository name: {name!r}')
        if not isinstance(homepage, (str, type(None))):
            raise ConfigError(f'{label} field "homepage" must be a string or null')
        if isinstance(homepage, str) and not homepage.strip():
            raise ConfigError(
                f'{label} field "homepage" must be a non-empty string or null'
            )
        if "protected" in entry and not isinstance(protected, bool):
            raise ConfigError(f'{label} field "protected" must be a boolean')
        normalized_name = name.casefold()
        if normalized_name in seen:
            raise ConfigError(f"duplicate repository name: {name}")

        seen.add(normalized_name)
        repositories.append(RepositoryConfig(name, homepage, protected))

    return repositories
