"""Mirror a tool's output to a log file as well as the terminal.

Long solves are the normal case here -- a full validation is a quarter of an
hour -- and they are often started in the background. Without this, watching
one means either staring at a terminal that owns the session or finding out
afterwards. Everything a tool prints goes to `logs/`, live, so a run can be
followed from another terminal with `tail -f`.

Output is unbuffered on purpose. Python line-buffers to a terminal but
block-buffers to a pipe or a file, which is what makes a backgrounded run
look like it has hung for the first several minutes.
"""

from __future__ import annotations

import atexit
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


class _Tee:
    """Write to two streams at once, flushing both every time."""

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary

    def write(self, text: str) -> int:
        self._primary.write(text)
        self._primary.flush()
        # A rewriting progress line is noise in a file; keep only the commits.
        self._secondary.write(text)
        self._secondary.flush()
        return len(text)

    def flush(self) -> None:
        self._primary.flush()
        self._secondary.flush()

    def isatty(self) -> bool:
        return hasattr(self._primary, "isatty") and self._primary.isatty()

    def __getattr__(self, name):
        return getattr(self._primary, name)


def start(name: str, argv=None) -> Path:
    """Begin mirroring stdout and stderr into ``logs/<name>.log``.

    Returns the log path so the tool can tell the user where to watch. Also
    refreshes ``logs/latest.log`` to point at this run, so there is always
    one predictable thing to tail.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    handle = path.open("w", encoding="utf-8", buffering=1)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    handle.write(f"# {name} started {stamp}\n")
    if argv:
        handle.write(f"# {' '.join(argv)}\n")
    handle.flush()

    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)

    latest = LOG_DIR / "latest.log"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(path.name)
    except OSError:
        pass          # a filesystem without symlinks is not worth failing over

    atexit.register(handle.close)
    return path
