"""Saving and reloading solved racing lines.

A converged line costs minutes to find and nothing to store, and almost
everything downstream wants to look at one again: plotting it, comparing two
of them, or -- the reason this exists -- calibrating a car against a fixed
line so that a change in lap time can be attributed to the car rather than
to the search wandering somewhere different.

A stored line is only valid for the exact geometry it was solved on, so each
file carries a fingerprint of the track and sampling it came from and
refuses to load against anything else. It also records which car it was
solved for. That one is *not* enforced, because reusing a line across car
variants is the point of the calibration workflow -- but it is reported, so
nobody has to guess.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from .track import Track

FORMAT_VERSION = 1


class StaleLineError(ValueError):
    """Raised when a stored line does not match the track it is loaded against."""


def track_fingerprint(track: Track) -> str:
    """Short hash of the geometry a line was solved on.

    Covers the curvature and width arrays and the sampling, which between
    them decide what a lap time means. Two tracks with the same fingerprint
    are interchangeable as far as a racing line is concerned.
    """
    digest = hashlib.sha256()
    digest.update(f"{FORMAT_VERSION}|{len(track)}|{track.length:.6f}|"
                  f"{int(track.closed)}".encode())
    for array in (track.curvature, track.w_left, track.w_right):
        digest.update(np.ascontiguousarray(array, dtype=np.float64).tobytes())
    return digest.hexdigest()[:16]


def save_line(path: str | Path, offset, track: Track, *,
              vehicle_name: str = "", lap_time: float | None = None,
              grip: float = 1.0, method: str = "", evaluations: int = 0,
              extra: dict | None = None) -> Path:
    """Write a solved line and enough context to know what it is."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "format": FORMAT_VERSION,
        "track": track.name,
        "fingerprint": track_fingerprint(track),
        "samples": len(track),
        "length_m": round(float(track.length), 4),
        "vehicle": vehicle_name,
        "lap_time_s": None if lap_time is None else round(float(lap_time), 6),
        "grip": float(grip),
        "method": method,
        "evaluations": int(evaluations),
        "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if extra:
        meta.update(extra)
    np.savez_compressed(path, offset=np.asarray(offset, dtype=float),
                        meta=np.array(json.dumps(meta)))
    return path


def load_line(path: str | Path, track: Track | None = None,
              strict: bool = True):
    """Read a stored line back, returning ``(offset, meta)``.

    With a ``track`` given, the fingerprint is checked. ``strict`` decides
    whether a mismatch raises or merely returns the offsets with the
    mismatch visible in the metadata -- a line solved on 4 m sampling is
    genuinely useless at 3 m, so raising is the default.
    """
    path = Path(path)
    with np.load(path, allow_pickle=False) as handle:
        offset = handle["offset"]
        meta = json.loads(str(handle["meta"]))
    if track is not None:
        actual = track_fingerprint(track)
        if actual != meta.get("fingerprint"):
            message = (
                f"{path.name} was solved on different geometry: stored "
                f"{meta.get('track')!r} ({meta.get('samples')} samples, "
                f"{meta.get('length_m')} m, fingerprint "
                f"{meta.get('fingerprint')}), loading against "
                f"{track.name!r} ({len(track)} samples, "
                f"{track.length:.4f} m, fingerprint {actual})")
            if strict:
                raise StaleLineError(message)
            meta["mismatch"] = message
        if len(offset) != len(track):
            raise StaleLineError(
                f"{path.name} has {len(offset)} offsets for a track of "
                f"{len(track)} samples")
    return offset, meta


def line_path(cache_dir: str | Path, track: Track, vehicle_name: str,
              grip: float = 1.0) -> Path:
    """Where a line for this track, car and grip level belongs."""
    tag = f"{track.name}-{vehicle_name or 'car'}".replace(" ", "_")
    if abs(grip - 1.0) > 1e-9:
        tag += f"-grip{grip:.3f}"
    return Path(cache_dir) / f"{tag}-{track_fingerprint(track)}.npz"


def find_line(cache_dir: str | Path, track: Track, vehicle_name: str,
              grip: float = 1.0) -> Optional[Path]:
    """The stored line for this combination, if there is one."""
    path = line_path(cache_dir, track, vehicle_name, grip=grip)
    return path if path.is_file() else None


def cached_racing_line(vehicle, track: Track, cache_dir: str | Path,
                       grip: float = 1.0, force: bool = False,
                       save: bool = True, verbose: bool = False, **kwargs):
    """Solve a racing line, or reuse the stored one for this exact geometry.

    Returns ``(RacingLine, source)`` where ``source`` is ``"cache"`` or
    ``"solved"``. A stored line is re-solved through the lap solver rather
    than trusted for its lap time, so the result is always consistent with
    the current physics -- only the expensive search is skipped.
    """
    from .qss import solve_lap
    from .racing_line import RacingLine

    path = line_path(cache_dir, track, vehicle.name, grip=grip)
    if path.is_file() and not force:
        offset, meta = load_line(path, track)
        lap = solve_lap(vehicle, track, offset=offset, grip=grip)
        if verbose:
            drift = (lap.lap_time - meta["lap_time_s"]
                     if meta.get("lap_time_s") else 0.0)
            note = f", {drift:+.3f} s against the stored time" if drift else ""
            print(f"  reusing {path.name}{note}")
        line = RacingLine(offset=offset, lap=lap,
                          seed_lap_time=meta.get("seed_lap_time_s",
                                                 lap.lap_time),
                          method=meta.get("method", "loaded from cache"),
                          evaluations=0)
        return line, "cache"

    from .racing_line import optimise_racing_line
    line = optimise_racing_line(vehicle, track, grip=grip, verbose=verbose,
                                **kwargs)
    if save:
        save_line(path, line.offset, track, vehicle_name=vehicle.name,
                  lap_time=line.lap_time, grip=grip, method=line.method,
                  evaluations=line.evaluations,
                  extra={"seed_lap_time_s": round(line.seed_lap_time, 6)})
        if verbose:
            print(f"  saved {path.name}")
    return line, "solved"
