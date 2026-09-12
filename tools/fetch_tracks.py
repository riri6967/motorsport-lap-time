#!/usr/bin/env python3
"""Download real circuit centrelines into the local track cache.

Geometry comes from the TUM Institute of Automotive Technology's
racetrack-database, which digitised OpenStreetMap traces into centrelines
with measured track widths. That database is LGPL-3.0, so the files are
fetched into a gitignored cache rather than committed here: this project
takes no position on its own licence yet, and vendoring copyleft data would
decide that question by accident.

    python3 tools/fetch_tracks.py            # the circuits used for validation
    python3 tools/fetch_tracks.py --all      # everything the database has
    python3 tools/fetch_tracks.py Monza Spa  # by name
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

BASE_URL = ("https://raw.githubusercontent.com/TUMFTM/"
            "racetrack-database/master/tracks/{name}.csv")
USER_AGENT = "motorsport-lap-time/0.1 (lap-time simulation research)"
CACHE = Path(__file__).resolve().parents[1] / "tracks" / "real"

# Circuits where a current LMP2/GT3/IndyCar lap record exists on the same
# layout. Austin is IndyCar's only entry -- see classes/indycar.yaml.
VALIDATION_SET = ("Monza", "Spa", "Silverstone", "Sakhir", "Catalunya", "Austin")

# "IMS" is the 2.5-mile Indianapolis Motor Speedway OVAL (its digitised
# length matches to within a metre), not the road course IndyCar's GP of
# Indianapolis actually runs -- and unusable for either without a banking
# term this project's vehicle/track model does not have. See
# classes/indycar.yaml.
ALL_TRACKS = (
    "Austin", "BrandsHatch", "Budapest", "Catalunya", "Hockenheim", "IMS",
    "Melbourne", "MexicoCity", "Montreal", "Monza", "MoscowRaceway",
    "Norisring", "Nuerburgring", "Oschersleben", "Sakhir", "SaoPaulo",
    "Sepang", "Shanghai", "Silverstone", "Sochi", "Spa", "Spielberg",
    "Suzuka", "YasMarina", "Zandvoort",
)

PROVENANCE = """# Track geometry cache

Downloaded by `tools/fetch_tracks.py`. Not committed: see that file for why.

- Source: TUMFTM/racetrack-database (github.com/TUMFTM/racetrack-database)
- Upstream origin: OpenStreetMap traces, smoothed; widths measured by the
  database authors
- Licence: LGPL-3.0
- Columns: `x_m, y_m, w_tr_right_m, w_tr_left_m`
- Retrieved: {today}

These are the circuits' Formula One or DTM configurations. Where a sports-car
championship runs a different layout -- a chicane in or out, a different pit
exit -- the geometry here is the wrong one, and any lap time computed on it
should be read with that in mind. `data/reference_laps.yaml` records which
comparisons are known to be layout-clean.
"""


def fetch(name: str, force: bool = False) -> Path:
    """Download one circuit into the cache, returning its path."""
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / f"{name}.csv"
    if target.exists() and not force:
        print(f"  {name:<16} already cached")
        return target
    request = urllib.request.Request(BASE_URL.format(name=name),
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    target.write_bytes(body)
    rows = body.decode("utf-8", "replace").count("\n")
    print(f"  {name:<16} {len(body) / 1024:6.1f} kB, {rows} points")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="circuits to fetch")
    parser.add_argument("--all", action="store_true",
                        help="fetch every circuit in the database")
    parser.add_argument("--force", action="store_true",
                        help="re-download even if already cached")
    parser.add_argument("--list", action="store_true",
                        help="list available circuits and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name in ALL_TRACKS:
            mark = "*" if name in VALIDATION_SET else " "
            print(f" {mark} {name}")
        print("\n* = used by tools/validate.py")
        return 0

    wanted = args.names or (ALL_TRACKS if args.all else VALIDATION_SET)
    unknown = [n for n in wanted if n not in ALL_TRACKS]
    if unknown:
        print(f"unknown circuit(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"try one of: {', '.join(ALL_TRACKS)}", file=sys.stderr)
        return 2

    print(f"fetching {len(wanted)} circuit(s) into {CACHE}")
    failures = []
    for name in wanted:
        try:
            fetch(name, force=args.force)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {name:<16} FAILED: {exc}", file=sys.stderr)
            failures.append(name)

    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "PROVENANCE.md").write_text(
        PROVENANCE.format(today=date.today().isoformat()), encoding="utf-8")

    if failures:
        print(f"\n{len(failures)} failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
