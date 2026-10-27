"""Saves the built search index to disk and loads it back on the next start.

Guarantees:
- A loaded index is exactly what was saved, or nothing is loaded. The file is
  written to a temporary name and renamed into place, so a crash mid-write
  leaves the previous file intact.
- A snapshot is only used by the exact code that wrote it. Every list's order
  comes from normalize.py and the ranking in index.py, so a snapshot from
  other code would silently misplace players. Its fingerprint covers the
  code of both files and the Unicode database normalization depends on;
  any difference means a full build instead. Comments, docstrings and
  formatting are left out, so editing those keeps the snapshot.

Loading takes a fraction of a build (about 0.1 s against 4 s at 100k
players, 0.9 s against 45 s at 1M). The snapshot does not have to be current:
it stores the Mongo time of the read it reflects, and the caller catches up
with the changes since (see sync.py).

The file is a pickle and only ever read from the API's own volume. Never
point SNAPSHOT_PATH at a location others can write to: loading a pickle
runs whatever code it names.
"""

import ast
import hashlib
import os
import pickle
import tempfile
import unicodedata
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from .index import PlayerSearchIndex

# NOTE The api service mounts its search-index volume on this directory
# (docker-compose.yml). Outside Docker it is a local directory (on Windows
# under the current drive's root), created on the first save when allowed;
# where it is not, saving only warns and every start builds.
SNAPSHOT_PATH = "/data/search/players.pickle"

# The files the order of every list depends on. Their code goes into the
# fingerprint, so a code change to them makes older snapshots unusable.
_SOURCES = ("index.py", "normalize.py")


def _code_digest(source: str) -> str:
    """Return the code of a module without comments, docstrings or formatting.

    Parsing drops comments and formatting; docstrings are the first
    statement of a module, class or function body when that is a bare
    string, and are removed here. The dump format may change with the
    Python version, which then costs one rebuild, never a wrong load.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.dump(tree)


def _fingerprint() -> str:
    digest = hashlib.sha256()
    for name in _SOURCES:
        source = Path(__file__).with_name(name).read_text(encoding="utf-8")
        digest.update(_code_digest(source).encode())
    digest.update(unicodedata.unidata_version.encode())
    return digest.hexdigest()


# Computed once: the sources cannot change while the process runs.
_FINGERPRINT = _fingerprint()


class Snapshot(NamedTuple):
    """A loaded index and the Mongo server time of the read it reflects."""

    index: PlayerSearchIndex
    read_at: datetime


def save_snapshot(path: str, state: dict, read_at: datetime) -> None:
    """Write an index state to path, replacing the previous snapshot atomically.

    Args:
        path (str): Target file. Its directory is created when missing.
        state (dict): A PlayerSearchIndex.to_state result.
        read_at (datetime): Mongo server time at the start of the read the
            state reflects. Changes from then on are read again on load.

    Raises:
        OSError: If the file cannot be written.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": _FINGERPRINT, "readAt": read_at, "state": state}
    # A temporary file of its own per save: the shutdown save can overlap a
    # refresh's save still running in its thread. Whichever renames last
    # wins, and either one is a complete, valid snapshot.
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            # Flushed to disk BEFORE the rename, or a power loss could leave
            # the new name pointing at an empty file.
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    except BaseException:
        with suppress(OSError):
            os.remove(temp)
        raise


def load_snapshot(path: str) -> Snapshot | None:
    """Load the snapshot at path, None if there is none usable.

    Never raises: a missing, foreign or damaged file only costs a full build,
    so it is logged and skipped.

    Args:
        path (str): The file save_snapshot wrote.

    Returns:
        Snapshot | None: The restored index, or None when the file is
            missing, written by other code, or unreadable.
    """

    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[WARNING] [SEARCH] Ignoring unreadable index snapshot {path}: {e}")
        return None

    if not isinstance(payload, dict) or payload.get("fingerprint") != _FINGERPRINT:
        print("[INFO] [SEARCH] Index snapshot is from other code, building anew")
        return None
    try:
        index = PlayerSearchIndex.from_state(payload["state"])
    except Exception as e:
        print(f"[WARNING] [SEARCH] Ignoring damaged index snapshot {path}: {e}")
        return None
    return Snapshot(index, payload["readAt"])
