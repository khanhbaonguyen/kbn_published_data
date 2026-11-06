# kbn_published_data

Public data files that Khanh Nguyen's websites read in the browser. Claude artifacts (or anyone with write access) update the files here; public web pages fetch them and render the latest numbers.

**Everything in this repository is public.** Only put data here that is meant to be seen on a public web page: no private notes, internal positions, account details or personal information.

## Layout

| Path | Used by | Contents |
|---|---|---|
| `midterms-2026/tracker.json` | suoilo.com/2026-midterms-tracker (and the embed below) | Race odds, Cook ratings, poll averages, proposition odds and called results. Rewritten by the Midterms 2026 Tracker refresh. |
| `vietamvote/midterms-2026-embed.html` | www.suoilo.com/2026-midterms-tracker (pasted into a Squarespace Code Block; the folder name is historical) | Read-only tracker page that fetches `midterms-2026/tracker.json` and re-checks it every 5 minutes |
| `vietamvote/midterms-2026.json` | nothing (unused placeholder) | Superseded by `midterms-2026/tracker.json` |
| `midterms-2026/ca-live.json` | the tracker page and embed | Live vote counts, written every 10 minutes by the "CA live results" GitHub Action |
| `scripts/ca_live_build.py`, `scripts/ca_sos_live.py` | "CA live results" Action | CA Secretary of State counts for CA House (`H-CA-*`) and Assembly (`A-CA-*`) races and propositions |
| `scripts/oc_rov_live.py` | "CA live results" Action | Orange County Registrar counts for races that carry an `oc` contest title (city council races) |
| `scripts/kalshi_refresh.py` | the Midterms 2026 Tracker refresh (Claude routine) | Fetches Kalshi odds for every race, proposition and control market (`refresh`) and builds `midterms-2026/tracker.json` (`publish`). Market tickers live in the tracker artifact's data, not in this script. |

Add a folder per site or project, one JSON file per page or feed.

## How a web page reads a file

Fetch the raw file. GitHub serves raw files with `Access-Control-Allow-Origin: *`, so a Squarespace Code Block can read them directly:

```
https://raw.githubusercontent.com/khanhbaonguyen/kbn_published_data/main/midterms-2026/tracker.json
```

Raw files are cached for about 5 minutes, so an update can take a few minutes to show. Add a cache-busting query string (for example `?t=` plus the current time) when fresher reads matter.

## How data gets updated

1. A Claude session (the Update button on the tracker artifact, or a scheduled refresh) regenerates the JSON file.
2. It commits the file to `main` in this repository, overwriting the previous version at the same path.
3. The public page picks up the new file on its next fetch.

For a Claude session to commit here, this repository must be connected to Claude through the Claude GitHub app and added to the session's environment.

To stop a page from updating, delete or empty its file. Nothing else depends on this repository.
