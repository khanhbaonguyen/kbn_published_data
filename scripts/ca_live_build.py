#!/usr/bin/env python3
"""Build midterms-2026/ca-live.json: live California vote counts for the tracker.

Usage (from the repo root):
    python3 scripts/ca_live_build.py [--allow-test] [--out PATH]

Reads the race and proposition list from midterms-2026/tracker.json, runs
scripts/ca_sos_live.py against the CA Secretary of State results API (CA House
"H-CA-*" and Assembly "A-CA-*" races, and propositions) and scripts/oc_rov_live.py
against the Orange County Registrar (races carrying an "oc" contest title), and writes
only the live counts to a separate file (default midterms-2026/ca-live.json):

    {"schema": 1, "updatedAt": "<ISO UTC>",
     "races": {"H-CA-45": <live>, ...},     # live = ca_sos_live.py's race.live
     "props": {"PROP-44": <live>, ...}}      # live = ca_sos_live.py's prop.live

Keeping the counts in their own file means the hourly tracker.json publish never
overwrites them and never collides with this job's commits. The VietAmVote embed
reads both files and merges them by id.

Last good values are kept when a contest fails to load or is still showing
pre-election test data. The file is rewritten only when a count changed; the
script prints "changed" or "unchanged" as the first word of its output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKER = os.path.join(ROOT, "midterms-2026", "tracker.json")
DEFAULT_OUT = os.path.join(ROOT, "midterms-2026", "ca-live.json")
LIVE_SCRIPT = os.path.join(ROOT, "scripts", "ca_sos_live.py")
OC_SCRIPT = os.path.join(ROOT, "scripts", "oc_rov_live.py")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main(argv):
    allow_test = "--allow-test" in argv
    out_path = argv[argv.index("--out") + 1] if "--out" in argv else DEFAULT_OUT

    tracker = load(TRACKER, None)
    if not tracker or not tracker.get("races"):
        print("error: tracker.json has no races")
        return 1
    prev = load(out_path, {"races": {}, "props": {}})

    # Only the contests this job covers, seeded with the last good counts.
    races = [{"id": r["id"], **({"live": prev["races"][r["id"]]} if r["id"] in prev.get("races", {}) else {})}
             for r in tracker["races"] if str(r.get("id", "")).startswith(("H-CA-", "A-CA-"))]
    props = [{"id": p["id"], "num": p["num"], **({"live": prev["props"][p["id"]]} if p["id"] in prev.get("props", {}) else {})}
             for p in tracker.get("props", [])]

    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "work.json")
        with open(work, "w") as f:
            json.dump({"races": races, "props": props}, f)
        cmd = [sys.executable, LIVE_SCRIPT, work] + (["--allow-test"] if allow_test else [])
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0:
            print("error: ca_sos_live.py failed:", (run.stderr or run.stdout).strip()[:500])
            return 1
        summary = run.stdout.strip()
        with open(work) as f:
            done = json.load(f)

    # Orange County local contests: races carrying an "oc" contest title.
    oc_races = [r for r in tracker["races"] if r.get("oc")]
    oc_live = {}
    if oc_races:
        cmd = [sys.executable, "-I", OC_SCRIPT] + [r["oc"] for r in oc_races] + (["--allow-test"] if allow_test else [])
        run = subprocess.run(cmd, capture_output=True, text=True)
        try:
            got = json.loads(run.stdout)
        except ValueError:
            got = {"contests": {}, "skipped": ["oc_rov_live.py failed: " + (run.stderr or run.stdout).strip()[:300]]}
        for r in oc_races:
            live = got["contests"].get(r["oc"])
            if live:
                display = {str(n).upper(): n for n in r.get("cands") or []}
                for c in live["c"]:
                    c["n"] = display.get(c["n"].upper(), c["n"].title())
                oc_live[r["id"]] = live
            elif r["id"] in prev.get("races", {}):
                oc_live[r["id"]] = prev["races"][r["id"]]  # keep last good count
        summary = summary + " oc=" + json.dumps({"ok": sorted(got["contests"]), "skipped": got.get("skipped", [])})

    new = {
        "races": {**{r["id"]: r["live"] for r in done["races"] if r.get("live")}, **oc_live},
        "props": {p["id"]: p["live"] for p in done["props"] if p.get("live")},
    }
    if new["races"] == prev.get("races", {}) and new["props"] == prev.get("props", {}):
        print("unchanged", summary)
        return 0

    out = {"schema": 1, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **new}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("changed", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
