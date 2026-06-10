from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path

_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.4)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text via a same-directory temporary file, then atomically replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding=encoding, dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
        _replace_with_retries(temp_name, path)
    finally:
        if temp_name:
            with suppress(OSError):
                Path(temp_name).unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
        _replace_with_retries(temp_name, path)
    finally:
        if temp_name:
            with suppress(OSError):
                Path(temp_name).unlink(missing_ok=True)


def _replace_with_retries(source: str, destination: Path) -> None:
    for delay in (*_REPLACE_RETRY_DELAYS, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)
