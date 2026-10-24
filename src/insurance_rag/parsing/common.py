"""Deterministic identities, workspace boundaries, and atomic artifacts."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def filesystem_path(file):
    """Extended Windows paths for IO without changing stored workspace paths."""
    file = Path(file).absolute()
    value = str(file)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\"+value[2:] if value.startswith("\\\\") else "\\\\?\\"+value
        return Path(value)
    return file


def display_path(file):
    value = str(file)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\"+value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def file_hash(file):
    digest = hashlib.sha256()
    with filesystem_path(file).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def workspace_path(value, workspace, *, must_exist=False):
    root = display_path(filesystem_path(workspace).resolve(strict=True))
    file = display_path(value)
    if not file.is_absolute():
        file = root / file
    file = display_path(filesystem_path(file).resolve(strict=must_exist))
    if not file.is_relative_to(root):
        raise ValueError("path_outside_workspace")
    return file


def relative_path(file, workspace):
    return display_path(file).resolve().relative_to(display_path(workspace).resolve()).as_posix()


def read_json(file):
    with filesystem_path(file).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def atomic_text(file, text):
    file = filesystem_path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=file.name + ".", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(file, value):
    atomic_text(file, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_jsonl(file, rows):
    atomic_text(file, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))
