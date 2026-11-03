#!/usr/bin/env python3
"""Kalshi odds refresh and public publish for the Midterms 2026 Tracker.

The tracker's data lives in the Claude artifact's database (tracker/current,
results/*), which only a Claude session can read or write. This script does
everything in between: the Claude session saves the documents to local JSON
files, runs this script, and writes back what it produces.

Usage (from the repo root):

    python3 scripts/kalshi_refresh.py refresh --tracker CURRENT.json \
        [--results RESULTS_DIR] [--ca-live midterms-2026/ca-live.json] \
        --out UPDATE.json

        Reads tracker/current (as saved by ArtifactData get with out_dir), fetches
        Kalshi prices for every race, prop and control market, and writes the
        fields to merge back into tracker/current:
            {races, props, control, kalshiUpdated, propsUpdated[, live]}
        cookAsOf, base, pollsUpdated, polls and propsConflict are left alone.
        `live` is included only when --ca-live names a file that exists and parses.

        Prints a JSON summary to stdout:
            {"races": {"ok": n, "failed": [ids]}, "props": {...},
             "control": {"senateD": p, "houseD": p}, "filled": [ids],
             "newResults": {doc_id: {"w", "at", "by"}}}
        newResults are settled markets with no existing results doc; the caller
        writes each one with ArtifactData set results/<doc_id>.

    python3 scripts/kalshi_refresh.py publish --tracker CURRENT.json \
        [--results RESULTS_DIR] [--out midterms-2026/tracker.json]

        Builds the public midterms-2026/tracker.json (compact JSON, schema 1) from
        tracker/current and the results collection. `live` is never copied: the
        public page reads midterms-2026/ca-live.json directly. Prints publishedAt.

RESULTS_DIR is the directory ArtifactData list wrote with out_dir (it may contain
a results/ subfolder); each <doc_id>.json there is one results document. A
missing directory means no results yet.

Probability rules: midpoint of yes bid and yes ask; last traded price when either
side is empty. A market ticker ending in -D or -R is that party, anything else
is an independent ("I"). Uses only Kalshi's public API, no login.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.elections.kalshi.com/trade-api/v2"
SETTLED = {"settled", "finalized", "determined"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_KEYS = ["kalshiUpdated", "pollsUpdated", "propsUpdated", "cookAsOf",
               "control", "base", "races", "props", "propsConflict"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(path: str, tries: int = 4):
    """GET a Kalshi API path. Returns parsed JSON, or None after retries/404."""
    for i in range(tries):
        try:
            req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            pass
        time.sleep(2 ** i)
    return None


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def prob(m: dict) -> float:
    bid, ask = num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars"))
    if bid > 0 and 0 < ask < 1:
        return round((bid + ask) / 2, 4)
    return round(num(m.get("last_price_dollars")), 4)


def party(ticker: str) -> str:
    s = ticker.rsplit("-", 1)[-1]
    return s if s in ("D", "R") else "I"


def settled_yes(m: dict) -> bool:
    return m.get("status") in SETTLED and m.get("result") == "yes"


def event_markets(event_ticker: str):
    d = fetch("/events/%s?with_nested_markets=true" % urllib.parse.quote(event_ticker))
    if not d:
        return None
    return (d.get("event") or {}).get("markets") or d.get("markets") or None


def series_for(race_id: str):
    """SEN-AZ -> SENATEAZ, GOV-AK -> GOVPARTYAK, H-CA-07 -> HOUSECA7, H-AK-AL -> HOUSEAKAL."""
    parts = race_id.split("-")
    if parts[0] == "H" and len(parts) == 3:
        dist = parts[2]
        return "HOUSE" + parts[1] + (str(int(dist)) if dist.isdigit() else dist)
    if parts[0] == "SEN" and len(parts) >= 2:
        return "SENATE" + parts[1]
    if parts[0] == "GOV" and len(parts) >= 2:
        return "GOVPARTY" + parts[1]
    return None


def load_results(path):
    """{doc_id: data} from an ArtifactData out_dir listing; {} if absent."""
    out = {}
    if not path:
        return out
    for base in (os.path.join(path, "results"), path):
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if name.endswith(".json"):
                    with open(os.path.join(base, name)) as f:
                        doc = json.load(f)
                    out[name[:-5]] = doc["data"] if isinstance(doc.get("data"), dict) else doc
            if out:
                break
    return out


def load_tracker(path: str) -> dict:
    with open(path) as f:
        doc = json.load(f)
    # Accept either the bare document or a {"data": {...}} wrapper.
    if "races" not in doc and isinstance(doc.get("data"), dict):
        doc = doc["data"]
    if not doc.get("races"):
        sys.exit("error: tracker document has no races")
    return doc


def refresh(args) -> int:
    t = load_tracker(args.tracker)
    have = load_results(args.results)
    stamp = now_iso()
    summary = {"races": {"ok": 0, "failed": []}, "props": {"ok": 0, "failed": []},
               "control": {}, "filled": [], "newResults": {}}

    def note_result(doc_id, w):
        if doc_id not in have and doc_id not in summary["newResults"]:
            summary["newResults"][doc_id] = {"w": w, "at": stamp, "by": "kalshi-settlement"}

    def candidates(markets, race_id):
        c = []
        for m in markets:
            p = party(m["ticker"])
            c.append({"n": m.get("yes_sub_title") or p, "p": p, "pr": prob(m)})
            if settled_yes(m):
                note_result(race_id, p)
        return c

    for r in t["races"]:
        k = r.get("kalshi")
        if k and k.get("ticker"):
            mk = event_markets(k["ticker"])
            if mk:
                k["c"] = candidates(mk, r["id"])
                summary["races"]["ok"] += 1
            else:
                summary["races"]["failed"].append(r["id"])  # keep prior prices
        else:
            ser = series_for(r["id"])
            d = ser and fetch("/events?series_ticker=%s&status=open&with_nested_markets=true" % ser)
            evs = (d or {}).get("events") or []
            if evs and evs[0].get("markets"):
                r["kalshi"] = {"ticker": evs[0]["event_ticker"], "c": candidates(evs[0]["markets"], r["id"])}
                summary["filled"].append(r["id"])
                summary["races"]["ok"] += 1

    for p in t.get("props", []):
        k = p.get("kalshi") or {}
        d = k.get("ticker") and fetch("/markets/%s" % urllib.parse.quote(k["ticker"]))
        m = (d or {}).get("market")
        if m:
            k["pr"] = prob(m)
            summary["props"]["ok"] += 1
            if m.get("status") in SETTLED and m.get("result") in ("yes", "no"):
                note_result(p["id"], "Y" if m["result"] == "yes" else "N")
        else:
            summary["props"]["failed"].append(p["id"])

    for key, label in (("senate", "senateD"), ("house", "houseD")):
        c = (t.get("control") or {}).get(key)
        if not c or not c.get("ticker"):
            continue
        mk = event_markets(c["ticker"]) or []
        dm = [m for m in mk if party(m["ticker"]) == "D"]
        if dm:
            c["kalshiD"] = prob(dm[0])
        else:
            summary.setdefault("controlFailed", []).append(c["ticker"])
        summary["control"][label] = c.get("kalshiD")

    update = {"races": t["races"], "props": t.get("props", []), "control": t.get("control", {}),
              "kalshiUpdated": stamp, "propsUpdated": stamp}

    if args.ca_live and os.path.isfile(args.ca_live):
        try:
            with open(args.ca_live) as f:
                cl = json.load(f)
            if isinstance(cl, dict):
                update["live"] = {"races": cl.get("races") or {}, "props": cl.get("props") or {}}
                summary["live"] = {"races": len(update["live"]["races"]), "props": len(update["live"]["props"])}
        except (json.JSONDecodeError, OSError):
            summary["live"] = "invalid, not copied"

    with open(args.out, "w") as f:
        json.dump(update, f)
    print(json.dumps(summary))
    return 0


def publish(args) -> int:
    t = load_tracker(args.tracker)
    out = {k: t[k] for k in PUBLIC_KEYS if k in t}
    out["results"] = {doc_id: {"w": d["w"], "at": d.get("at")}
                      for doc_id, d in load_results(args.results).items() if d.get("w")}
    out["schema"] = 1
    out["publishedAt"] = now_iso()
    text = json.dumps(out, separators=(",", ":"))
    check = json.loads(text)
    if not check.get("races"):
        sys.exit("error: refusing to publish a tracker with no races")
    with open(args.out, "w") as f:
        f.write(text)
    print(out["publishedAt"])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="fetch Kalshi prices and write the tracker/current update")
    r.add_argument("--tracker", required=True)
    r.add_argument("--results")
    r.add_argument("--ca-live")
    r.add_argument("--out", required=True)
    p = sub.add_parser("publish", help="build the public midterms-2026/tracker.json")
    p.add_argument("--tracker", required=True)
    p.add_argument("--results")
    p.add_argument("--out", default=os.path.join(ROOT, "midterms-2026", "tracker.json"))
    args = ap.parse_args(argv)
    return refresh(args) if args.cmd == "refresh" else publish(args)


if __name__ == "__main__":
    sys.exit(main())
