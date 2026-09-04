<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: license-checker-rseidelsohn | npm view license-checker-rseidelsohn version | 5.0.1 -->
<!-- probe: pip-licenses | curl -s https://pypi.org/pypi/pip-licenses/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" | 5.5.5 -->
# licensing scaffold-first reference (researched 2026-08-30)

Covers the two licensing jobs every repo has: **choosing and generating the project's own license**, and **auditing what the dependency tree's licenses allow**. All commands below were run live this session.

## Choosing and generating a LICENSE

The license text comes from GitHub's official licenses API — never retype or paraphrase license text:

```bash
gh api licenses --jq '.[].key'          # the catalog (mit, apache-2.0, agpl-3.0, ...)
gh api licenses/mit --jq '.body' > LICENSE
```

Then fill the `[year]` and `[fullname]` placeholders. Which license is a business decision: proprietary/all-rights-reserved is the default for FairStandard product code (no LICENSE file = all rights reserved, but say so in the README); MIT or Apache-2.0 for things deliberately open-sourced — Apache-2.0 when patent grants matter. Surface the choice to Christopher for anything public; don't default it.

## Auditing dependency licenses (all run live)

| Ecosystem | Command | Observed |
|---|---|---|
| npm | `npx -y license-checker-rseidelsohn --summary` (add `--failOn 'GPL;AGPL'` in CI) | summary of the tree (e.g. MIT: 7, Apache-2.0: 2 on a fresh Hono app) |
| Python/uv | `uv run --with pip-licenses pip-licenses` | full table incl. transitive (MPL, BSD, Apache spotted on requests' tree) |
| Go | `go run github.com/google/go-licenses@latest report ./...` | per-module license CSV; chi → MIT |
| Rust | `cargo license` (install via `cargo install cargo-license`) | NOT run this session — verify before relying |

## Never hand-write

- License text — always from `gh api licenses/<key>`; a retyped license is legally a different document.
- Per-dependency license attributions/NOTICE files — generate them (`license-checker-rseidelsohn --files`, `go-licenses save`), don't compile by hand.

## Thorough licensing checklist

1. Every repo states its terms: a LICENSE file (open source) or a README line saying proprietary (closed). Absence-with-silence is the only wrong state.
2. Run the audit for the repo's ecosystem before the first public artifact or customer deploy; wire `--failOn` (or an equivalent deny-list) into CI so a copyleft dependency can't slip in unnoticed.
3. Shipping desktop/mobile builds bundle dependencies — the audit is part of the shipping checklist (`shipping-desktop-mobile.md`), not just the repo's.
4. A dependency reporting "Unknown" is a finding, not noise — chase it to the package's repo before shipping.

## Traps

**Your own module shows as `Unknown` in go-licenses until a LICENSE file exists** — the tool scans for license-ish filenames; an unlicensed fresh module errors loudly. That error is the checklist item, not a tool bug. (Observed 2026-08-30.)

**The classic `license-checker` npm package is unmaintained; the maintained fork is `license-checker-rseidelsohn`** (5.0.1 observed working). Memory reaches for the old name. (Observed 2026-08-30.)

**`pip-licenses` needs to run INSIDE the environment it audits** — `uv run --with pip-licenses pip-licenses` puts it in the project venv; a bare `uvx pip-licenses` audits its own empty env and reports nothing useful. (Observed 2026-08-30.)

## AI and agent resources

- `https://api.github.com/licenses` — the machine-readable catalog (what `gh api licenses` wraps); choosealicense.com is the human guide behind it.
- SPDX identifiers (`MIT`, `Apache-2.0`) are the canonical spelling in package manifests — validate against the gh catalog keys.
