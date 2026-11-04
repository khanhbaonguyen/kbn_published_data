#!/usr/bin/env python3
"""Live Orange County vote counts for local contests in the Midterms 2026 Tracker.

Usage (from the repo root):
    python3 -I scripts/oc_rov_live.py "CONTEST TITLE" ["CONTEST TITLE" ...] [--allow-test] [--html FILE]

The Orange County Registrar of Voters publishes election-night results as one large
HTML page on livevoterturnout.com (embedded at https://ocvote.gov/results/current-election-results).
This script finds that page through ocvote.gov (falling back to the last known
address), then reads each named contest, matched case-insensitively against the
contest title the county shows, for example
"CITY OF GARDEN GROVE Member, City Council District 4".

Prints one JSON object to stdout: {"contests": {title: live}, "skipped": [...]}, where

    live = {"src": "OC ROV", "rep": "<ballots counted text>", "at": "<ISO UTC>",
            "c": [{"n": NAME, "p": "N", "v": votes, "pct": percent}]}   # sorted by votes

Names are the county's (upper case, incumbent "*" removed); the caller maps them to
display names. Reports stamped before polls close (8 p.m. Pacific, Nov 3, 2026) are
treated as test data and skipped unless --allow-test. --html reads a saved copy of the
results page instead of fetching (for testing).
"""
from __future__ import annotations

import html as htmllib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

OCVOTE = "https://ocvote.gov/results/current-election-results"
FALLBACK = "https://www.livevoterturnout.com/ENR/orangecaenr/24/en/phkv4sV_Index_24.html"
PACIFIC = ZoneInfo("America/Los_Angeles")
POLLS_CLOSE = datetime(2026, 11, 3, 20, 0, tzinfo=PACIFIC)
UA = "Mozilla/5.0 (compatible; kbn-published-data tracker)"


def get(url: str, timeout: int = 60) -> str:
    r = subprocess.run(["curl", "-sS", "-f", "-L", "-m", str(timeout), "-A", UA, url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "curl failed").strip()[:200])
    return r.stdout


def results_url() -> str:
    try:
        page = get(OCVOTE, 30)
        m = re.search(r'src="(https://www\.livevoterturnout\.com/ENR/orangecaenr/[^"]+Index[^"]*\.html)"', page)
        if m:
            return htmllib.unescape(m.group(1))
    except Exception:
        pass
    return FALLBACK


def num(s):
    s = str(s or "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(t)).strip().upper()


def parse(page: str, titles, allow_test: bool):
    out, skipped = {}, []
    m = re.search(r'id="lblLastPublishedDateTime"[^>]*>\s*([^<]+?)\s*<', page)
    when = None
    if m:
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p"):
            try:
                when = datetime.strptime(m.group(1).strip(), fmt).replace(tzinfo=PACIFIC)
                break
            except ValueError:
                continue
    if when is None:
        return out, ["unreadable 'Website Updated' time"]
    if not allow_test and when < POLLS_CLOSE:
        return out, [f"report stamped {when.isoformat()} is before polls close (test data)"]
    at = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = re.search(r"Ballots Counted</span>[\s\S]{0,300}?<span>\s*([\d,]+)\s*</span>", page)
    rep = ("Countywide ballots counted " + b.group(1)) if b else "Countywide count"

    heads = {norm(t): rid for rid, t in
             re.findall(r'<span id="raceHeading_(\d+)" class="sr-only">\s*Contest:\s*(.*?)(?:,\s*VOTE FOR[^<]*)?</span>', page)}
    for title in titles:
        rid = heads.get(norm(title))
        if not rid:
            skipped.append(f"{title}: contest not found")
            continue
        names, votes, pcts, order = {}, {}, {}, []
        for cid, name in re.findall(
                r'data-race="%s" data-contestant="(\d+)" data-candidate="true"[^>]*>\s*<span[^>]*>([^<]*)</span>' % rid, page):
            if cid not in names:
                names[cid] = htmllib.unescape(name).strip().lstrip("*").strip()
                order.append(cid)
        for cid, v in re.findall(r'data-race="%s" data-contestant="(\d+)" data-votes="true"[^>]*>([^<]*)<' % rid, page):
            votes.setdefault(cid, num(v))
        for cid, v in re.findall(r'data-race="%s" data-contestant="(\d+)" data-percent="true"[^>]*>([^<]*)<' % rid, page):
            pcts.setdefault(cid, num(v))
        cands = [{"n": names[c], "p": "N", "v": int(votes[c]), "pct": pcts.get(c)}
                 for c in order if votes.get(c) is not None]
        if not cands:
            skipped.append(f"{title}: no candidate rows")
            continue
        total = sum(c["v"] for c in cands)
        for c in cands:
            if c["pct"] is None:
                c["pct"] = round(100 * c["v"] / total, 2) if total else 0.0
        cands.sort(key=lambda c: -c["v"])
        out[title] = {"src": "OC ROV", "rep": rep, "at": at, "c": cands}
    return out, skipped


def main(argv):
    allow_test = "--allow-test" in argv
    html_file = argv[argv.index("--html") + 1] if "--html" in argv else None
    titles = [a for a in argv if not a.startswith("--") and a != html_file]
    if not titles:
        print(__doc__)
        return 2
    try:
        page = open(html_file, encoding="utf-8").read() if html_file else get(results_url())
    except Exception as e:
        print(json.dumps({"contests": {}, "skipped": [f"fetch failed: {e}"]}))
        return 0
    out, skipped = parse(page, titles, allow_test)
    print(json.dumps({"contests": out, "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
