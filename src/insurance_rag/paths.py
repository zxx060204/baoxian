"""Resolve the local workspace without tying it to a package install path."""
import os
from pathlib import Path


def get_project_root() -> Path:
    explicit = os.environ.get("INSURANCE_PROJECT_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("INSURANCE_PROJECT_ROOT must be an existing directory")
        return root
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "insurance_rag" / "__init__.py").is_file():
                return candidate.resolve()
    raise RuntimeError("Run from the project directory or set INSURANCE_PROJECT_ROOT")
