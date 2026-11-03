#!/usr/bin/env python3
"""Add live California vote counts to the Midterms 2026 Tracker data.

Usage:
    python3 -I scripts/ca_sos_live.py TRACKER_JSON [--out OUT_JSON] [--allow-test]

TRACKER_JSON is a JSON object with "races" and "props" arrays (the tracker/current
document, or any object holding those two arrays). The script fetches the
California Secretary of State's public results API (https://api.sos.ca.gov/returns)
and adds a "live" field to:

  * every race whose id starts with "H-CA-"  (from /returns/us-rep/district/<n>)
  * every proposition                        (from /returns/ballot-measures)

    race.live = {"src": "CA SOS", "rep": "<reporting text>", "at": "<ISO UTC>",
                 "c": [{"n": name, "p": "D"|"R"|"I", "v": votes, "pct": percent}]}
    prop.live = {"src": "CA SOS", "rep": "...", "at": "...",
                 "y": yes votes, "n": no votes, "yp": yes percent, "np": no percent}

Before polls close the state's feed has served test numbers, so any report stamped
earlier than 8 p.m. Pacific on Nov 3, 2026 is ignored (--allow-test turns that off
for dry runs). A contest that fails to load keeps whatever "live" value it already
had. The file is rewritten in place unless --out is given. A one-line JSON summary
goes to stdout. Uses curl so it follows the session's proxy settings.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API = "https://api.sos.ca.gov/returns"
PACIFIC = ZoneInfo("America/Los_Angeles")
POLLS_CLOSE = datetime(2026, 11, 3, 20, 0, tzinfo=PACIFIC)
PARTY = {"dem": "D", "democratic": "D", "rep": "R", "republican": "R"}


def fetch(path: str):
    r = subprocess.run(["curl", "-sS", "-f", "-m", "25", "-H", "Accept: application/json", f"{API}/{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "curl failed").strip()[:200])
    return json.loads(r.stdout)


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_time(text):
    """'November 3, 2026, 9:14 p.m.' (Pacific) -> aware datetime, or None."""
    if not text:
        return None
    t = str(text).strip().replace("a.m.", "AM").replace("p.m.", "PM").replace("noon", "12:00 PM")
    t = re.sub(r"\s+", " ", t)
    for fmt in ("%B %d, %Y, %I:%M %p", "%B %d, %Y, %I %p", "%B %d, %Y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=PACIFIC)
        except ValueError:
            continue
    return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def stamp(block, allow_test):
    """Returns (reporting text, ISO time) or raises if the report is pre-election test data."""
    when = parse_time(block.get("ReportingTime") or block.get("reportingTime"))
    if not allow_test:
        if when is None:
            raise ValueError(f"unreadable ReportingTime {block.get('ReportingTime')!r}")
        if when < POLLS_CLOSE:
            raise ValueError(f"report stamped {when.isoformat()} is before polls close (test data)")
    return (block.get("Reporting") or block.get("reporting") or "").strip(), iso(when)


def pick_statewide(data):
    if isinstance(data, list):
        return next((d for d in data if "statewide" in str(d.get("raceTitle", "")).lower()), data[0] if data else {})
    return data


def house_live(district: int, allow_test: bool):
    race = pick_statewide(fetch(f"us-rep/district/{district}"))
    rep, at = stamp(race, allow_test)
    cands = []
    for c in race.get("candidates") or []:
        votes = num(c.get("Votes"))
        if votes is None:
            continue
        cands.append({"n": str(c.get("Name", "")).strip(),
                      "p": PARTY.get(str(c.get("Party", "")).strip().lower(), "I"),
                      "v": int(votes), "pct": num(c.get("Percent"))})
    if not cands:
        raise ValueError("no candidates in response")
    total = sum(c["v"] for c in cands)
    for c in cands:
        if c["pct"] is None:
            c["pct"] = round(100 * c["v"] / total, 1) if total else 0.0
    cands.sort(key=lambda c: -c["v"])
    return {"src": "CA SOS", "rep": rep, "at": at, "c": cands}


def props_live(allow_test: bool):
    data = fetch("ballot-measures")
    outer = data if isinstance(data, dict) else {}
    measures = outer.get("ballot-measures", []) if isinstance(data, dict) else data
    out = {}
    for m in measures or []:
        n = str(m.get("Number", "")).strip().lstrip("0")
        y, no = num(m.get("yesVotes")), num(m.get("noVotes"))
        if not n or y is None or no is None:
            continue
        block = m if (m.get("ReportingTime") or m.get("Reporting")) else outer
        rep, at = stamp(block, allow_test)
        tot = y + no
        out[n] = {"src": "CA SOS", "rep": rep, "at": at, "y": int(y), "n": int(no),
                  "yp": num(m.get("yesPercent")) if num(m.get("yesPercent")) is not None else (round(100 * y / tot, 1) if tot else 0.0),
                  "np": num(m.get("noPercent")) if num(m.get("noPercent")) is not None else (round(100 * no / tot, 1) if tot else 0.0)}
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    allow_test = "--allow-test" in argv
    out_path = None
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]
        args = [a for a in args if a != out_path]
    if not args:
        print(__doc__)
        return 2
    src = args[0]
    doc = json.load(open(src))
    summary = {"races": [], "props": 0, "skipped": []}

    for r in doc.get("races", []):
        m = re.fullmatch(r"H-CA-(\d+)", r.get("id", ""))
        if not m:
            continue
        try:
            r["live"] = house_live(int(m.group(1)), allow_test)
            summary["races"].append(r["id"])
        except Exception as e:
            summary["skipped"].append(f"{r['id']}: {e}")

    try:
        pl = props_live(allow_test)
        for p in doc.get("props", []):
            live = pl.get(str(p.get("num")))
            if live:
                p["live"] = live
                summary["props"] += 1
    except Exception as e:
        summary["skipped"].append(f"ballot-measures: {e}")

    json.dump(doc, open(out_path or src, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
