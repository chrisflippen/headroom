#!/usr/bin/env python3
"""Freshness check for scaffold-first reference pages.

Every reference page starts with a machine-readable block:

  <!-- freshness verified=YYYY-MM-DD baseline=YYYY-MM-DD -->
  <!-- probe: <name> | <command printing the current latest version> | <seen> -->

'verified' = when the page's commands were last run live.
'baseline' = when the probe 'seen' values were captured.
A page with no probe lines is checked by age only.

Usage:
  freshness.py --quick [page ...]   ages for all pages; probes only for the
                                    named pages (basename, .md optional).
                                    Run this at skill start for the page(s)
                                    you are about to rely on.
  freshness.py --sweep              run every probe on every page (slow);
                                    the weekly drift sweep.

Exit codes: 0 = fresh, 1 = drift or stale found, 2 = a page is missing its
freshness block or a probe command failed (fix the page, don't ignore it).
"""
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REFS = Path(__file__).parent / "references"
STALE_DAYS = 60

BLOCK_RE = re.compile(r"<!-- freshness verified=(\d{4}-\d{2}-\d{2}) baseline=(\d{4}-\d{2}-\d{2}) -->")
# The command field may itself contain " | " (curl ... | python3 ...), so the
# name is everything before the FIRST separator and 'seen' everything after the
# LAST one: lazy first group, greedy middle.
PROBE_RE = re.compile(r"<!-- probe: (.+?) \| (.+) \| (.+?) -->")


def parse(path):
    text = path.read_text()
    m = BLOCK_RE.search(text)
    if not m:
        return None
    return {
        "verified": date.fromisoformat(m.group(1)),
        "baseline": date.fromisoformat(m.group(2)),
        "probes": PROBE_RE.findall(text),
    }


def run_probe(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90)
        out = r.stdout.strip().splitlines()
        return out[-1].strip() if out else ""
    except subprocess.TimeoutExpired:
        return ""


def major(v):
    m = re.match(r"[^\d]*(\d+)", v)
    return m.group(1) if m else v


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("--quick", "--sweep"):
        sys.exit(__doc__)
    mode, targets = args[0], {a.removesuffix(".md") for a in args[1:]}

    pages = sorted(REFS.glob("*.md"))
    today = date.today()
    stale, drift, broken = [], [], []

    for p in pages:
        meta = parse(p)
        if meta is None:
            broken.append(f"{p.name}: MISSING freshness block — add one before relying on this page")
            continue
        age = (today - meta["verified"]).days
        if age > STALE_DAYS:
            stale.append(f"{p.name}: verified {meta['verified']} ({age} days ago, threshold {STALE_DAYS})")
        if mode == "--sweep" or p.stem in targets:
            for name, cmd, seen in meta["probes"]:
                cur = run_probe(cmd)
                if not cur:
                    broken.append(f"{p.name}: probe '{name}' returned nothing — command broken or offline")
                elif cur != seen:
                    tag = " MAJOR" if major(cur) != major(seen) else ""
                    drift.append(f"{p.name}: {name} {seen} -> {cur}{tag}")

    for line in broken + stale + drift:
        print(line)
    if not (broken or stale or drift):
        checked = "all pages" if mode == "--sweep" else (", ".join(sorted(targets)) or "ages only")
        print(f"fresh: {len(pages)} pages within {STALE_DAYS} days, probes checked: {checked}")

    if broken:
        sys.exit(2)
    if stale or drift:
        sys.exit(1)


if __name__ == "__main__":
    main()
