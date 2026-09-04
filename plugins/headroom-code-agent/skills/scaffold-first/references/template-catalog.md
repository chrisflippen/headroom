<!-- freshness verified=2026-08-22 baseline=2026-08-30 -->
# Template catalog (vetted 2026-08-22, round 2)

Admission to this catalog is by proof, never by fame: every template listed as admitted was actually generated on this machine, unattended, and its fresh output had to pass the standard's own checks (sync from lockfile, lint, format-check, typecheck, tests) as generated. Rejected templates stay listed with their disqualifying evidence so nobody reaches for a famous-but-stale name. Star counts and version claims below were resolved live on the vetting date and will drift - re-verify before load-bearing use. House templates (ours, encoding this standard's checklists directly) are the second half of this program and will supersede third-party templates for our own project shapes once built.

## python-projects

General Python project/package templates -- the current Cookiecutter and Copier field, checked live (GitHub API for staleness/health, actual unattended generation + `uv sync` / `ruff check` / `ruff format --check` / typecheck / `pytest` on the fresh output) against the scaffold-first python-uv standard. Checked 2026-08-22.

### Admitted

**osprey-oss/cookiecutter-uv** (formerly `fpgmaas/cookiecutter-uv` -- old slug now redirects) -- 1,323 stars, pushed 2026-08-15.
```
uvx cookiecutter gh:osprey-oss/cookiecutter-uv --no-input -o myproj
```
uv + hatchling, ruff, mypy, pytest, tox-uv, mkdocs. `uv sync`, `ruff check`, `mypy`, `pytest` all clean on fresh output. CI installs with `uv sync --frozen` against the committed lockfile.

**audreyfeldroy/cookiecutter-pypackage** -- 4,593 stars, pushed 2026-08-17. The "OG" package template, fully modernized off setup.py/tox.
```
uvx cookiecutter gh:audreyfeldroy/cookiecutter-pypackage --no-input -o myproj
```
uv + hatchling, ruff, ty, typer. `ruff check`/`pytest` clean; `ty check` surfaces one small real bug in the template's own release script (a PEP 723 python-version mismatch) -- worth a bug report upstream, not disqualifying. CI runs ruff/ty/pytest via `uv run` on pinned-SHA Actions.

**scientific-python/cookie** -- 408 stars, pushed 2026-08-17. The scientific-Python community's own cookie.
```
uvx cookiecutter gh:scientific-python/cookie --no-input -o myproj
# then: git init && git add -A && git commit -m init   (hatch-vcs needs git history for the version)
```
hatchling + hatch-vcs, proper `[dependency-groups]` (test/dev/docs), curated ruff ruleset, strict mypy config. Everything clean on fresh output; CI runs `uv sync` + `uv run pytest --cov`.

**jlevy/simple-modern-uv** -- 295 stars, pushed 2026-08-15, only 3 open issues. Copier template built for agent-driven workflows (ships AGENTS.md/CLAUDE.md).
```
uvx --exclude-newer "14 days" copier@9.17.0 copy --defaults --trust gh:jlevy/simple-modern-uv myproj
# then: git init && git add -A && git commit -m init   (uv-dynamic-versioning needs git history)
```
hatchling + uv-dynamic-versioning, ruff, basedpyright, pytest. Everything clean on fresh output. CI runs `uv sync --locked --all-extras --all-groups` -- the strictest lockfile-only install available.

### Rejected

**pawamoy/copier-uv** -- 157 stars, actively maintained (pushed 2026-08-20), but the fresh unmodified output fails its own checks: `ruff check` finds 8 errors and `ruff format --check` flags 7 of 30 files, all in static template-owned files (not user-input-dependent) -- the committed ruff config doesn't enable the rule codes its own boilerplate's `noqa` comments assume are on. Generation itself also requires an undocumented-at-invocation extra package (`copier-template-extensions`) plus explicit `-d` answers since `--defaults` alone can't satisfy the required `project_name` question.

**drivendataorg/cookiecutter-data-science** -- 10,013 stars, the most recognized name in the family, still actively pushed (2026-08-07). Rejected anyway: this is the famous-but-stale failure mode the catalog exists to catch. The maintainers deprecated the plain `cookiecutter` CLI for it (generation now errors outright); switching to their own `ccds` CLI with `environment_manager=uv` explicitly selected still produces a project with zero tests, zero pytest config, zero typechecker, zero CI (no `.github` directory at all), dev tooling dumped straight into `[project.dependencies]` with no dependency-group separation, and a `requires-python` pin locked to patch-level `3.10.x` only. None of the standard's checklist items pass on a fresh generation.

## Family: python-web-ml (verified 2026-08-22)

### Admitted

**fastapi/full-stack-fastapi-template** — anchor, FastAPI + React + SQLModel + Postgres
Generate: `git clone https://github.com/fastapi/full-stack-fastapi-template.git myproj && cd myproj/backend && uv sync --dev`
The template's own documented copier command (`copier copy ... --trust -f`) is currently **broken** — reproducible `FileNotFoundError` on dangling `.agents/skills/fastapi`/`sqlmodel` symlinks that only resolve inside the maintainers' dev `.venv`. Their README has quietly moved to plain clone-and-configure as the primary path. On that path: `uv sync` clean, `ruff check` clean, `mypy app` clean (21 files, 0 errors), pytest collects 60 tests. CI installs via `uv sync` throughout. Repo pushed 2026-08-21, 45k stars, 8 open issues.

**cookiecutter/cookiecutter-django** — Django, batteries-included
Generate: `uvx cookiecutter gh:cookiecutter/cookiecutter-django --no-input -o out`
Clean unattended generation, no extra deps. Post-gen hook itself installs via uv ("Installing python dependencies using uv..."). Output has `uv.lock`, Django 6.0.8, `requires-python==3.14.*`. `ruff check` clean; `manage.py check` clean once DB env vars are set (expected). With `ci_tool=Github`, generated CI uses `astral-sh/setup-uv@v5` + `uv sync --locked`. Released 2026.8.18 on 2026-08-19 — releases land every 1-5 days (last twelve releases, GitHub Releases API 2026-08-22).

**kedro-org/kedro-starters (spaceflights-pandas)** — the ML-pipeline pick (conditional admission)
Repo health: 86 stars, pushed 2026-06-29 (the stalest push in this catalog), 6 open issues (GitHub API, 2026-08-22).
Generate: `KEDRO_DISABLE_TELEMETRY=1 uvx kedro new --starter=spaceflights-pandas -n <name>`
Clean unattended generation. **Caveat:** scaffolds `setuptools` + `requirements.txt`, no `uv.lock` — confirmed via `kedro new --help` that the CLI has no uv path today, so this is dated tooling by the standard's own bar, not an invocation mistake. Dependencies still resolve current and clean (`uv pip install -r requirements.txt`, 160+ packages, no conflicts). After editable install: `pytest -q` → 4 passed, 88% coverage. Default `ruff check` (tools=none) found 7 findings in the starter's own shipped test files — not lint-clean out of the box. **Still admitted** as the standard's designated Kedro pick (see ml-pipelines.md): no uv-native Kedro starter exists today, so the lockfile and lint gaps are tracked tooling debt to fix after generation, not a disqualifying defect.

### Rejected

**wemake-services/wemake-django-template** — dated tooling, generation friction
The standard's documented invocation (`uvx cookiecutter ... --no-input`) fails outright: `Unable to load extension: No module named 'jinja2_git'`. Their own README confirms this template requires injecting an extra package the plain command doesn't carry. Even once generated (via `uvx --with jinja2-git cookiecutter ...`), the output scaffolds **Poetry** (`poetry-core` build backend + `poetry.lock`), not uv — disqualifying per the standard's explicit uv-vs-poetry criterion. Repo itself is actively maintained (pushed 2026-08-17), so this is a tooling-currency rejection, not an abandonment one.

## js-web template catalog (researched 2026-08-22)

Scope: JS/TS project **templates** — arguments to already-vetted creators (`create-next-app --example`), plus standalone starters not covered elsewhere. Does not re-vet the creators themselves, and does not re-vet Nuxt's `--template` (minimal/content/ui/module, already graded in `js-web-extended.md`) or SvelteKit's `sv create --template` (minimal/demo/library, already graded in `sveltekit.md`) — see those pages.

Every candidate below was generated live on this machine (macOS, Node v26.7.0, npm 11.19.0), stdin closed (`< /dev/null`), 2026-08-22.

### Admitted

#### Next.js Boilerplate (ixartz/Next-js-Boilerplate) — via `create-next-app --example`
- **Generate:** `npx create-next-app@latest <dir> -e https://github.com/ixartz/Next-js-Boilerplate --use-npm --disable-git`
- **Result:** exit 0, no prompt. `npm ci` installed 1139 packages clean in 12s. `npm run check:types` (`tsc --noEmit --pretty`) — exit 0. `npm run lint` (Ultracite/Biome, type-aware) — exit 0, "All matched files use the correct format." `npx vitest run` — the one real unit test passed; the only failure was a Storybook browser-mode test needing `npx playwright install` (missing local browser binary — an environment gap, not a scaffold defect).
- **Versions pinned:** `next@16.3.1`, `typescript@7.0.2` (current GA), `react@19.2.8`, `vitest@4.1.10`, `@playwright/test@1.62.1` — all current as of this check.
- **CI:** `.github/workflows/CI.yml` runs build/lint/unit/storybook/e2e jobs; its own `setup-project` composite action runs `npm ci` keyed on `hashFiles('package-lock.json')` — installs from the lockfile, not a bare `npm install`.
- **Health:** github.com/ixartz/Next-js-Boilerplate — pushed 2026-08-20 (2 days before this check), 13,052 stars, 4 open issues, not archived.

#### Turborepo starter (create-turbo, `vercel/turborepo`)
- **Generate:** `npx create-turbo@latest <dir> -m npm --no-git`
- **Result:** exit 0, no prompt, ~10s. `npm run check-types` (turbo-orchestrated `tsc --noEmit` across 3 packages) — exit 0. `npm run lint` (turbo-orchestrated `eslint --max-warnings 0`) — exit 0. `npm run build` — exit 0, both `apps/web` and `apps/docs` compiled and pre-rendered successfully.
- **Versions pinned:** `next@16.3.0` (current), `react@19.2.0`, `typescript@5.9.2`, `eslint@9.39.1` (functionally fine, but note: ESLint 9 went end-of-life 2026-08-06 per the standard's own js-ts-core.md — flag as tooling debt, not disqualifying since it lints correctly today).
- **CI:** none generated by default (no `.github/`) — this is a bare monorepo scaffold, not a full app starter; note the gap if the project needs CI day one.
- **Health:** github.com/vercel/turborepo — pushed same day as this check (2026-08-22), 30,970 stars, 17 open issues, not archived.

### Rejected

#### T3 Stack (create-t3-app, `t3-oss/create-t3-app`)
- **Generate attempted:** `npx create-t3-app@latest <dir> --CI --tailwind true --trpc true --appRouter true --eslint true --drizzle true --dbProvider sqlite --noGit` — generated fine (exit 0, ~18s), so it clears the "can it generate unattended" bar, but fails the tooling-currency bar.
- **Disqualifying evidence:** pins `next@^15.2.3` (current stable is 16 — Turbopack-by-default, current `AGENTS.md` generation, etc., all missing); pins `eslint@^9.23.0`, which is past ESLint's own end-of-life date (2026-08-06, per the standard's js-ts-core.md); pins `typescript@^5.8.2` against a current GA of 7.0. Worse: the generated `package.json`'s own `lint` script is `next lint` — and running it prints, live: **"`next lint` is deprecated and will be removed in Next.js 16."** A template whose default lint command is a command Next.js's own next major version removes is not current tooling.
- **Source:** `t3-oss/create-t3-app` npm `create-t3-app@7.40.0` published 2025-11-05; repo pushed 2025-12-13, 29,091 stars, 132 open issues, not archived — actively used, but the generated output itself is stale.

#### Next.js SaaS Starter (`nextjs/saas-starter`) — via `create-next-app --example`
- **Generate attempted:** `npx create-next-app@latest <dir> -e https://github.com/nextjs/saas-starter --use-npm --disable-git` — generated fine (exit 0), clears unattended generation.
- **Disqualifying evidence:** pins `"next": "15.6.0-canary.59"` — a **canary pre-release build**, not a released version, in a template meant to be a stable starting point. `package.json` has **no `lint` script, no `typecheck` script, and no test runner at all** (no vitest/jest/playwright in dependencies) — there is nothing to run and grade. No `.github/` directory — no CI. Three of the standard's four grading questions (current tooling, checks run, CI from lockfile) all fail on inspection alone.
- **Source:** github.com/nextjs/saas-starter — pushed 2025-12-11 (over 8 months before this check), 16,050 stars, 51 open issues, not archived. The "official"-sounding `nextjs` org name is exactly the fame trap this catalog exists to catch — famous org, stale unreleased pin, no checks.

#### Epic Stack (`epicweb-dev/epic-stack`, via `npx epicli new`)
- **Generate attempted:** `npx epicli@latest new <dir> < /dev/null` — **hangs indefinitely, but late.** Corrected evidence (re-reproduced twice, 2026-08-22, with process inspection during the hang): the CLI auto-detects the non-interactive shell ("Shell is not interactive. Using default options."), prints substantial progress output, and completes template copy, dependency install, and the remix.init script (build plus database migrate/generate) in roughly 90 seconds with a fully populated project directory — then hangs forever at `playwright install` (browser binary download), which never returns and has no timeout and no flag to skip.
- **Verdict:** rejected for agent use — the process never returns control, so it cannot run unattended even though generation itself succeeds. The upstream repo is healthy (pushed 2026-08-09, 5,546 stars, 13 open issues, not archived); the disqualifier is that final never-returning step, not the scaffold.
- **Source:** local reproduction, this session, `epicli@1.3.5` (npm, published 2025-10-22).

### Notes for the catalog maintainer
- Nuxt's `--template` and SvelteKit's `sv create --template` families are already fully graded elsewhere in the standard (`js-web-extended.md`, `sveltekit.md`) — not duplicated here per instruction.
- `create-t3-app` and Epic Stack are both large, actively-discussed communities (29k and 5.5k stars respectively) — proof that community size and upstream repo activity do not by themselves predict whether the *generated output* is current or whether the *generator* runs unattended. That gap is exactly why generation + grading, not fame, is the bar.

