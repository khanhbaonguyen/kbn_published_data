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
    # State-run races that lie entirely inside Orange County: their county contest title, used
    # only to size the ballots-left estimate (the SOS count is what the pages show).
    OC_STATE_RACES = {"A-CA-70": "MEMBER OF THE STATE ASSEMBLY 70th District"}
    oc_races = [r if r.get("oc") else {**r, "oc": OC_STATE_RACES[r["id"]]}
                for r in tracker["races"] if r.get("oc") or r.get("id") in OC_STATE_RACES]
    oc_live, oc_votes, oc_ballots = {}, {}, None
    if oc_races:
        cmd = [sys.executable, "-I", OC_SCRIPT] + [r["oc"] for r in oc_races] + (["--allow-test"] if allow_test else [])
        run = subprocess.run(cmd, capture_output=True, text=True)
        try:
            got = json.loads(run.stdout)
        except ValueError:
            got = {"contests": {}, "skipped": ["oc_rov_live.py failed: " + (run.stderr or run.stdout).strip()[:300]]}
        for r in oc_races:
            live = got["contests"].get(r["oc"])
            if live and live.get("ballots"):
                oc_ballots = live["ballots"]
            if live and r["id"].startswith(("H-CA-", "A-CA-")):
                # State-run race inside Orange County: the SOS count is shown; the county count only sizes the estimate.
                oc_votes[r["id"]] = sum(c["v"] for c in live["c"])
                continue
            if live:
                oc_votes[r["id"]] = sum(c["v"] for c in live["c"])
                display = {str(n).upper(): n for n in r.get("cands") or []}
                for c in live["c"]:
                    c["n"] = display.get(c["n"].upper(), c["n"].title())
                oc_live[r["id"]] = live
            elif r["id"] in prev.get("races", {}) and not r["id"].startswith(("H-CA-", "A-CA-")):
                oc_live[r["id"]] = prev["races"][r["id"]]  # keep last good count
        summary = summary + " oc=" + json.dumps({"ok": sorted(got["contests"]), "skipped": got.get("skipped", [])})

    new = {
        "races": {**{r["id"]: r["live"] for r in done["races"] if r.get("live")}, **oc_live},
        "props": {p["id"]: p["live"] for p in done["props"] if p.get("live")},
    }
    # Ballots left: the county's estimate (its "What's Left to Process" page, or a manual
    # midterms-2026/oc-left-manual.json {"total", "asOf"[, "final"]}) shared out by each race's
    # portion of ballots counted so far. Every Orange County race is marked "in progress"
    # until that manual file says final.
    if oc_races:
        manual = load(os.path.join(ROOT, "midterms-2026", "oc-left-manual.json"), None)
        left = None
        if manual and manual.get("total"):
            left = {"total": int(manual["total"]), "asOf": manual.get("asOf"), "src": "manual"}
        else:
            run = subprocess.run([sys.executable, "-I", OC_SCRIPT, "--left"], capture_output=True, text=True)
            try:
                left = json.loads(run.stdout).get("left")
            except ValueError:
                left = None
        if not left and prev.get("ocLeft"):
            left = prev["ocLeft"]  # keep last good county estimate
        final = bool(manual and manual.get("final"))
        for r in oc_races:
            lv = new["races"].get(r["id"])
            if not lv:
                continue
            lv = dict(lv)
            lv["inProgress"] = not final
            v = oc_votes.get(r["id"])
            if left and v and oc_ballots and not final:
                lv["left"] = {"n": int(round(left["total"] * v / oc_ballots)), "county": left["total"], "asOf": left.get("asOf")}
            else:
                lv.pop("left", None)
            new["races"][r["id"]] = lv
        if left:
            new["ocLeft"] = left
        summary += " left=" + json.dumps(left)

    # Two-candidate races with a ballots-left estimate: how the latest batch of counted
    # votes split ("batch", carried forward until the count changes again) and the share
    # of the remaining ballots the trailing candidate needs to pull ahead ("need").
    for rid, lv in list(new["races"].items()):
        if not lv.get("left") or len(lv.get("c") or []) != 2:
            continue
        lv = dict(lv)
        cur = {c["n"]: c["v"] for c in lv["c"]}
        old_lv = prev.get("races", {}).get(rid) or {}
        old = {c["n"]: c.get("v") for c in old_lv.get("c") or []}
        if set(old) == set(cur) and all(isinstance(v, int) for v in old.values()) and old != cur:
            diff = {n: cur[n] - old[n] for n in cur}
            n = sum(diff.values())
            if n > 0 and all(d >= 0 for d in diff.values()):
                lv["batch"] = {"n": n, "at": lv.get("at"), "sh": {k: round(100 * d / n, 1) for k, d in diff.items()}}
        elif old_lv.get("batch") and old == cur:
            lv["batch"] = old_lv["batch"]
        lead, trail = sorted(lv["c"], key=lambda c: -c["v"])
        margin, rem = lead["v"] - trail["v"], lv["left"]["n"]
        lv["need"] = {"n": trail["n"], "pct": round(100 * (rem + margin) / (2 * rem), 1)} if rem > 0 and margin > 0 else None
        new["races"][rid] = lv

    if all(new.get(k) == prev.get(k) for k in ("races", "props", "ocLeft")):
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
