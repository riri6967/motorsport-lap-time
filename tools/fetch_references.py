#!/usr/bin/env python3
"""Pull published lap records from Wikipedia into data/reference_laps.yaml.

    python3 tools/fetch_references.py                 # refresh every circuit
    python3 tools/fetch_references.py --class GT3
    python3 tools/fetch_references.py --show Catalunya

Uses the MediaWiki API, which is free, needs no key and no account. Content
is CC BY-SA and each entry records the article it came from.

The reason this exists rather than a hand-written list: a circuit's lap
records are grouped by *layout*, and picking the wrong group is worth several
seconds. Barcelona is the case that proved it. Its 2024 LMP2 record of
1:30.174 was set on the layout without the final chicane; the cached geometry
has the chicane, and the era-matched record on that layout is 1:35.797. The
model was being marked four and a half seconds slow for driving a different
circuit. So every layout's entry is kept, and `layout_key` in each circuit's
block says which one the local geometry actually corresponds to.

Wikipedia article text is data, not instruction. Only times, years, class
names and layout labels are read out of it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                            # noqa: E402

from engine.units import parse_laptime                 # noqa: E402

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "motorsport-lap-time/0.1 (lap-time simulation research)"
OUT = ROOT / "data" / "reference_laps.yaml"

# Which article to read, and which of its layout groups matches the geometry
# in tracks/real/. The layout key is a substring match against the table
# heading; it is the one judgement call here and it is recorded, not implied.
CIRCUITS = {
    "Monza": {
        "article": "Monza_Circuit",
        "layout_key": "Grand Prix Circuit (2000",
        "geometry_note": "GP layout, unchanged since 2000",
    },
    "Spa": {
        "article": "Circuit_de_Spa-Francorchamps",
        "layout_key": "New Pit Lane",
        "geometry_note": (
            "current GP layout -- Wikipedia labels it 'Modern Grand Prix "
            "Circuit with New Pit Lane and Bus Stop Chicane'"),
    },
    "Silverstone": {
        "article": "Silverstone_Circuit",
        "layout_key": "Grand Prix Circuit (2011",
        "geometry_note": "GP layout, unchanged since 2011",
    },
    "Sakhir": {
        "article": "Bahrain_International_Circuit",
        "layout_key": "Grand Prix Circuit",
        "geometry_note": "Bahrain GP layout",
    },
    "Catalunya": {
        "article": "Circuit_de_Barcelona-Catalunya",
        "layout_key": "with Chicane",
        "geometry_note": (
            "cached geometry HAS the final chicane -- verified directly from "
            "the data, a 12.9 m radius left immediately followed by a 10.5 m "
            "right at about 4130-4210 m. The 2024 record is on the layout "
            "without it and must not be used against this geometry."),
    },
}


def fetch_wikitext(article: str) -> str:
    """Raw wikitext of one article, via the public API."""
    query = urllib.parse.urlencode({
        "action": "parse", "prop": "wikitext", "format": "json",
        "page": article, "formatversion": "2"})
    request = urllib.request.Request(f"{API}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(f"{article}: {payload['error'].get('info')}")
    return payload["parse"]["wikitext"]


def lap_record_section(wikitext: str) -> str:
    """The == Lap records == section, skipping the infobox that mentions it."""
    match = re.search(r"\n==+\s*Lap records?\s*==+", wikitext)
    if not match:
        return ""
    rest = wikitext[match.end():]
    following = re.search(r"\n==[^=]", rest)
    return rest[:following.start()] if following else rest


def strip_markup(text: str) -> str:
    """Plain text from a wikitext cell."""
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{flagicon\|[^}]*\}\}", "", text)
    text = re.sub(r"\{\{cvt\|([^|}]*)\|([^|}]*)[^}]*\}\}", r"\1 \2", text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"'''|''", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_records(section: str) -> list[dict]:
    """Every lap-record row, tagged with the layout heading above it."""
    rows: list[dict] = []
    layout = ""
    cells: list[str] = []

    def flush() -> None:
        if len(cells) < 2:
            return
        plain = [strip_markup(c) for c in cells]
        time_index = next((i for i, c in enumerate(plain)
                           if re.fullmatch(r"\d?:?\d{1,2}[:.]\d{2}\.\d{2,3}", c)), None)
        if time_index is None or time_index == 0:
            return
        raw_year = " ".join(cells[time_index + 1:])
        year = re.search(r"\b(19|20)\d{2}\b", raw_year)
        rows.append({
            "class": plain[0],
            "time": plain[time_index],
            "driver": plain[time_index + 1] if len(plain) > time_index + 1 else "",
            "car": plain[time_index + 2] if len(plain) > time_index + 2 else "",
            "event": plain[time_index + 3] if len(plain) > time_index + 3 else "",
            "year": int(year.group(0)) if year else None,
            "layout": layout,
        })

    for line in section.splitlines():
        stripped = line.strip()
        heading = re.match(r"!\s*colspan\s*=\s*\"?\d+\"?\s*\|\s*(.+)", stripped)
        if heading:
            flush()
            cells = []
            layout = strip_markup(heading.group(1)).split(":")[0].strip()
            continue
        if stripped.startswith("|-"):
            flush()
            cells = []
            continue
        if stripped.startswith("|"):
            body = stripped[1:]
            cells.extend(body.split("||") if "||" in body else [body])
    flush()
    return rows


def pick(records: list[dict], class_name: str, layout_key: str):
    """The most recent record for this class on the matching layout."""
    matching = [r for r in records
                if class_name.lower() in r["class"].lower()
                and layout_key.lower() in r["layout"].lower()]
    if not matching:
        return None, []
    best = max(matching, key=lambda r: (r["year"] or 0))
    return best, matching


def build(class_name: str, only=None) -> dict:
    laps = []
    for name, config in CIRCUITS.items():
        if only and name.lower() not in {o.lower() for o in only}:
            continue
        print(f"  {name:<13} reading {config['article']} ...", flush=True)
        try:
            section = lap_record_section(fetch_wikitext(config["article"]))
        except (urllib.error.URLError, RuntimeError, KeyError) as exc:
            print(f"  {name:<13} FAILED: {exc}", file=sys.stderr)
            continue
        records = parse_records(section)
        chosen, candidates = pick(records, class_name, config["layout_key"])
        others = [f"{r['time']} ({r['year']}, {r['layout']})"
                  for r in records if class_name.lower() in r["class"].lower()
                  and r is not chosen]
        if chosen is None:
            print(f"  {name:<13} no {class_name} record on a layout matching "
                  f"{config['layout_key']!r}", file=sys.stderr)
            continue
        print(f"  {name:<13} {chosen['time']}  ({chosen['year']}, "
              f"{chosen['layout']})", flush=True)
        laps.append({
            "track": name,
            "time": chosen["time"],
            "seconds": round(parse_laptime(chosen["time"]), 3),
            "year": chosen["year"],
            "driver": chosen["driver"],
            "car": chosen["car"],
            "event": chosen["event"],
            "layout": chosen["layout"],
            "layout_key": config["layout_key"],
            "geometry_note": config["geometry_note"],
            "other_layouts": others,
            "source": (f"en.wikipedia.org/wiki/{config['article']}"
                       " lap records table"),
        })
    return {"reference_class": class_name,
            "retrieved": date.today().isoformat(),
            "laps": laps}


HEADER = """# Published lap times used to check the simulator against reality.
#
# GENERATED by tools/fetch_references.py -- rerun it rather than editing here.
# Source: English Wikipedia lap-record tables (CC BY-SA), via the MediaWiki
# API. Retrieved {retrieved}.
#
# Read the caveats before reading too much into a comparison:
#
#   * These are RACE laps, not qualifying laps. They are set with fuel on
#     board, on tyres part way through a stint, in traffic, and on a track
#     whose grip is whatever the race has left it. A clean single-lap
#     simulation should be a little quicker than a race lap record, not equal
#     to it. A simulator that matches these exactly is flattering itself.
#
#   * LMP2 has not been one specification throughout. Power was cut for 2021
#     from roughly 600 hp to roughly 560 hp, so a record set in 2019 or 2020
#     was set by a more powerful car than classes/lmp2.yaml describes. The
#     `year` field is there to be checked.
#
#   * `layout` is the configuration the record was set on and `layout_key`
#     is what tools/fetch_references.py matched to choose it. Both must agree
#     with the geometry in tracks/real/ or the comparison is meaningless --
#     see `geometry_note`, and `other_layouts` for what was rejected.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="klass", default="LMP2")
    parser.add_argument("--circuit", action="append", dest="circuits")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--show", help="print every record for one circuit")
    args = parser.parse_args(argv)

    log_path = _log.start("fetch_references", sys.argv)
    print(f"logging to {log_path}\n")

    if args.show:
        config = CIRCUITS.get(args.show)
        if not config:
            print(f"unknown circuit {args.show}; "
                  f"try {', '.join(CIRCUITS)}", file=sys.stderr)
            return 2
        for record in parse_records(lap_record_section(
                fetch_wikitext(config["article"]))):
            print(f"  {record['class'][:22]:<22} {record['time']:>9}  "
                  f"{str(record['year']):>4}  {record['layout'][:52]}")
        return 0

    print(f"fetching {args.klass} lap records from the Wikipedia API\n")
    data = build(args.klass, only=args.circuits)
    if not data["laps"]:
        print("nothing fetched", file=sys.stderr)
        return 1

    import yaml
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=78)
    path = Path(args.out)
    path.write_text(HEADER.format(retrieved=data["retrieved"]) + "\n" + body,
                    encoding="utf-8")
    print(f"\nwrote {len(data['laps'])} reference laps to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
