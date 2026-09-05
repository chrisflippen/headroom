<!-- freshness verified=2026-08-22 baseline=2026-09-04 -->
<!-- probe: uv | curl -s https://pypi.org/pypi/uv/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" | 0.12.9 -->
<!-- probe: ruff | curl -s https://pypi.org/pypi/ruff/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" | 0.16.6 -->
<!-- probe: pyrefly | curl -s https://pypi.org/pypi/pyrefly/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" | 1.2.0 -->
# python-uv scaffold-first reference (verified 2026-08-22, round 5)

## Official setup commands

| Step | Command | What it generates |
|---|---|---|
| Start a new project | `uv init [name] [--app \| --lib \| --package \| --no-package \| --bare] [--script]` | `pyproject.toml`, `.python-version`, `.gitignore`, `README.md`, a git repo, and either `src/<pkg>/__init__.py` (packaged), `main.py` (flat app), or nothing extra (`--bare`, pyproject.toml only) |
| Pin the Python version | `uv python pin <version>` | `.python-version` |
| Add a runtime dependency | `uv add <package>` | edits `pyproject.toml` `[project.dependencies]`, updates `uv.lock`, syncs `.venv` |
| Add a dev-only dependency | `uv add --dev <package>` | edits `pyproject.toml` `[dependency-groups].dev`, updates `uv.lock`, syncs `.venv` |
| Re-resolve the lockfile | `uv lock` | rewrites `uv.lock` |
| Sync the environment to the lockfile | `uv sync` | creates/updates `.venv` to match `uv.lock` |
| Type-check config | `uv run pyrefly init --non-interactive` (after `uv add --dev pyrefly`) | `pyrefly.toml`, or a `[tool.pyrefly]` table in `pyproject.toml` if one doesn't have a standalone file yet; if it finds an existing mypy/pyright config it offers to migrate the settings over. `--non-interactive` skips the y/N auto-suppression prompt `pyrefly init` otherwise shows when the project has 100 or fewer existing type errors, so it never hangs an agent with no terminal attached |
| pre-commit starter config | `uv run pre-commit sample-config > .pre-commit-config.yaml` (after `uv add --dev pre-commit`) | a starter `.pre-commit-config.yaml` with a handful of generic hooks (trailing whitespace, end-of-file-fixer, check-yaml, etc.) — you're expected to add tool-specific hooks (ruff, pyrefly, pytest) to it afterward |
| Install the git hook | `uv run pre-commit install` | `.git/hooks/pre-commit` |
| Database migrations setup | `uv run alembic init alembic` (after `uv add --dev alembic`) (variants: `--template async`, `--template multidb`, `--template pyproject`) | `alembic.ini`, `alembic/env.py`, `alembic/README`, `alembic/script.py.mako`, `alembic/versions/` |
| New migration | `uv run alembic revision --autogenerate -m "message"` | a new file in `alembic/versions/` |

**No init command exists for ruff or pytest.** As of August 2026, `ruff init` is still an open, unimplemented feature request (astral-sh/ruff#12111), and pytest has never had a config-generating command. For these two, hand-writing the `[tool.ruff]` / `[tool.ruff.lint]` / `[tool.ruff.format]` block and the `[tool.pytest.ini_options]` block in `pyproject.toml` is expected and is not a scaffold-first violation — there is no generator to defer to.

**Run pre-commit and alembic with `uv run`, not `uvx`, once they're project dependencies.** Both are added to the project with `uv add --dev`, which pins a version in `uv.lock`. `uvx` (an alias for `uv tool run`) always runs from its own separate, disposable cache and ignores that pin — so if you ran either tool via `uvx` after adding it with `uv add --dev`, the version that actually runs would not be the version the project locked. `uv run` is the only invocation that uses the pinned version. (Source: https://docs.astral.sh/uv/concepts/tools/)

## Choosing

**Packaged app, flat app, or library — pick by what you're shipping.** `uv init` with no flags now gives you a *packaged* application (a `src/<name>/__init__.py` layout with a `[build-system]` table) — that's been the default since uv v0.12; older training data or older repos may remember an unpackaged default, so don't trust memory here. Use plain `--app` (the default) when the code will be installed into its own environment and run as an entry point — most web servers and CLIs. Use `--no-package` when you just want a script-style app with a flat `main.py` and no build step — small tools, one-off automation. Use `--lib` when the project will be imported by other packages or published to PyPI; libraries are always packaged, no `--no-package` opt-out. Use `--bare` only when you want nothing but a `pyproject.toml` and plan to lay out everything else yourself.

**Type checker: pyrefly vs ty vs mypy/pyright.** Pyrefly (Meta) is the one this reference standardizes on: it has a stable 1.0 release, a real `init` command that migrates existing mypy/pyright config automatically, and a documented agent-loop recipe (AGENTS.md directive + Claude Code Stop hook). Astral's own `ty` is tempting for stack consistency with uv/Ruff, but it is still explicitly labeled "in beta" by Astral itself — pick it only if you're comfortable with a moving target, not for a project that needs a settled toolchain today. mypy and pyright remain fine if a codebase is already built around them, but neither ships an init/config-generation command or an agent-loop guide, so migrating an existing large codebase to pyrefly (via `pyrefly init`, which reads the existing mypy/pyright config directly) is usually less work than it looks.

**Running pre-commit: `uv run` vs `uvx`.** If pre-commit is added as a project dev-dependency (`uv add --dev pre-commit`), always drive it with `uv run pre-commit ...` — that's the only invocation that actually uses the version pinned in `uv.lock`. `uvx pre-commit ...` is the right call only if you deliberately *don't* want pre-commit tracked as a project dependency at all — uv treats `uvx` environments as disposable and separate from the project, by design.

**Alembic template.** `generic` (the default, no flag needed) is right for a single database. Reach for `--template async` only if the project's DB driver is already async (asyncpg, aiomysql, etc.) — it changes `env.py` to use an async engine. Use `--template multidb` for genuinely multiple independent databases in one app, which is rarer than it sounds. `--template pyproject` (new in Alembic 1.16) moves Alembic's own config out of `alembic.ini` and into `pyproject.toml` — pick it if the project already tries to keep all tool config in one file and doesn't mind Alembic being the exception that needs the change.

**Dependency groups: `--dev` vs a named group.** `uv add --dev <pkg>` is shorthand for `uv add --group dev <pkg>` — fine for a single undifferentiated "everything I need locally" bucket. Once lint tooling, type-checking tooling, and test tooling start pulling in different, non-overlapping dependencies (or you want CI to install only the test group in one job and only the lint group in another), use named groups (`uv add --group test pytest`, `uv add --group lint ruff`) instead of dumping everything into `dev` — PEP 735's `[dependency-groups]` table supports as many named groups as you want, not just `dev`.

## Never hand-write

- `pyproject.toml`'s `[project]` and `[build-system]` tables — these come from `uv init` / `uv add`, not typed by hand
- `uv.lock` — fully machine-generated; uv itself warns this file should never be edited manually
- `.python-version` — from `uv python pin`
- `pyrefly.toml` / `[tool.pyrefly]` — from `pyrefly init`; after that, only touch the specific fields it left open (excludes, suppressions)
- `alembic.ini` and the `alembic/` scaffold (`env.py`, `script.py.mako`, `README`) — from `alembic init`; inside `env.py` only edit the clearly marked customization points (e.g. wiring in your model's metadata or DB URL), don't rewrite the file
- `alembic/versions/*.py` migration files — from `alembic revision --autogenerate`, never written from scratch
- `.git/hooks/pre-commit` — from `pre-commit install`
- `.pre-commit-config.yaml` — the skeleton must come from `pre-commit sample-config`; only the hook list you add on top is hand-written

## Thorough setup checklist

1. `uv init` with the right project kind for what you're building
2. `uv python pin` so the Python version is locked and committed
3. Runtime deps added with `uv add`, dev deps with `uv add --dev`
4. `uv.lock` committed, never edited by hand
5. `uv sync` run once so `.venv` actually matches the lockfile before writing any feature code
6. `pyrefly init` run, `pyrefly check` passing (or has reviewed, intentional suppressions)
7. `ruff check` and `ruff format --check` both passing, with rules chosen (not just defaults) in `pyproject.toml`
8. `pytest` configured with `testpaths`, at least one real passing test in `tests/`
9. `.pre-commit-config.yaml` built from `sample-config` plus ruff/pyrefly/pytest hooks, `pre-commit install` run, `pre-commit run --all-files` passing
10. If there's a database: `alembic init` run before any migration is hand-written, first migration made with `--autogenerate`
11. README documents the exact commands to rebuild the environment from a clean clone

## Traps

**Redirecting `uv run <tool> ... > file` on a cold environment can pour uv's own build/install chatter into the file.** On the first `uv run` in a fresh project, uv may resolve, build, and install packages before the tool executes, and those progress lines can land ahead of the tool's real output inside your redirect target — a stress-test run captured `Building ...` / `Installed 1 package` prepended to an otherwise-correct `pre-commit sample-config` skeleton, corrupting `.pre-commit-config.yaml`. Warm the environment first (`uv sync`, or run the tool once without a redirect) before any `uv run ... > file` whose output becomes a config, or pass `uv run -q` to suppress uv's own output; then verify the file starts with the tool's expected first line. (Source: recorded run, scaffold-stress fan-out 2026-08-22, run mech-6.)

**`pyrefly init` can silently wait for a keypress on any project that already has type errors.** Past config creation, `pyrefly init` runs an initial `pyrefly check` and, if it finds 100 or fewer errors, drops into a y/N prompt asking whether to auto-suppress them — with no TTY attached, this hangs an agent rather than failing. The provider's own CLI ships a `--non-interactive` flag specifically for this: "Run without interactive prompts, using safe defaults (decline all). Useful for CI, scripted workflows, or running init before check in automated pipelines." Always pass it when running `pyrefly init` unattended, even on what looks like a brand-new project. (Source: facebook/pyrefly repo, `commands/init.rs`, `InitArgs`.)

**Do not invent a `ruff init` command.** Ruff has never shipped a config-generating command, and the open feature request for one (astral-sh/ruff#12111) is still unresolved as of this check — labeled "needs-decision," no fix scheduled. An agent that "remembers" a `ruff init` from another tool's convention, or that pastes in an old `[tool.ruff]` block from memory, will either invent a nonexistent subcommand or ship stale rule codes that Ruff has since removed. Hand-writing the `[tool.ruff]` table is the correct, expected path — there's no generator to defer to. (Source: https://github.com/astral-sh/ruff/issues/12111)

**Never hand-edit `uv.lock`.** uv's own docs are explicit: "`uv.lock` is a human-readable TOML file but is managed by uv and should not be edited manually." A hand-edited pin (say, to "fix" one package version) produces a lockfile that no longer matches what uv's resolver would actually produce, and the drift is invisible until the next `uv sync` or a teammate's clean install breaks. Use `uv add`, `uv remove`, or `uv lock --upgrade-package` instead. (Source: https://docs.astral.sh/uv/concepts/projects/layout/)

**`uv init`'s default output changed in v0.12 — don't trust older mental models of it.** Prior to that release, a bare `uv init` produced an unpackaged app (flat `main.py`, no `[build-system]`); today the same bare command produces a packaged app with a `src/<name>/__init__.py` layout and a `[build-system]` table. An agent working from stale training data will misdescribe what a fresh `uv init` actually generates unless it checks current docs first. Don't type the `[project]` / `[build-system]` tables from memory either way — let `uv init` and `uv add` write them so they match what the resolver actually expects. (Source: https://docs.astral.sh/uv/concepts/projects/init/ — "Prior to v0.12, uv did not define a build system for applications by default"; general `uv init` behavior per https://docs.astral.sh/uv/reference/cli/#uv-init)

**A dev-dependency added with `uv add --dev` and then run with `uvx` can silently be a different version than the one in `uv.lock`.** `uvx` (== `uv tool run`) always runs from its own disposable, cached environment, entirely separate from the project's `.venv` — it does not read `uv.lock` at all. If a tool is meant to be pinned and reproducible for the team, invoke it with `uv run <tool>`, not `uvx <tool>`; mixing the two for the same tool means "what's locked" and "what actually ran" can quietly diverge. (Source: https://docs.astral.sh/uv/concepts/tools/)

**Writing a full `.pre-commit-config.yaml` from memory instead of starting from `sample-config`.** `pre-commit sample-config` just prints a fixed, known-good starter block — trailing whitespace, end-of-file-fixer, check-yaml, and other baseline hygiene hooks. A hand-written file tends to skip those baseline checks entirely, not just the tool-specific ones you're supposed to add on top. (Source: pre-commit's own `sample-config` command output, cited via the pre-commit CLI reference at https://pre-commit.com/)

**Hand-writing `alembic/env.py` or a migration file instead of running `alembic init` and `alembic revision --autogenerate`.** A hand-written migration doesn't reflect the actual current model state and drifts from the real schema the moment anyone changes a model. Let `alembic init` scaffold the environment and `alembic revision --autogenerate` generate each migration; only edit the clearly marked customization points inside `env.py`. (Source: https://alembic.sqlalchemy.org/en/latest/tutorial.html)

**Fighting `pyrefly init` instead of re-running it.** Running `pyrefly init` once and then hand-editing broad swaths of `pyrefly.toml` afterward works against the tool — it has its own migration and suppression features for exactly this. Re-run `init` (with `--non-interactive` if unattended) or use its suppression tooling instead of manually rewriting large parts of the config. (Source: facebook/pyrefly repo, `commands/init.rs`, `InitArgs` — the same interactive/suppression behavior documented above.)

**Skipping `uv sync` and running `python file.py` or `pip install` directly inside `.venv`.** This lets the environment quietly stop matching `uv.lock` — nothing installed that way is reflected in the lockfile, so a teammate's clean `uv sync` won't reproduce it. Run `uv sync` once before writing any feature code, and use `uv run` for one-off commands so the project environment stays authoritative. (Source: https://docs.astral.sh/uv/concepts/projects/sync/)

## AI and agent resources

**uv, Ruff, and ty (Astral)** ship official `llms.txt` index files on their docs site — fetch these before nontrivial work so an agent finds the real current page instead of guessing from stale training data:
- uv: `https://docs.astral.sh/uv/llms.txt`
- Ruff: `https://docs.astral.sh/ruff/llms.txt`
- ty: `https://docs.astral.sh/ty/llms.txt`

Each file is a table of contents; the docs note that appending an explicit `index.md` path to a linked page returns clean markdown instead of rendered HTML. No `llms-full.txt` variant exists for any of the three (all returned 404), and there is no official Astral MCP server or hosted docs-search tool — only unrelated third-party MCP wrappers exist, which don't count as official.

**Contributing to Astral's own repos** (not using their tools downstream) has real agent support: both `astral-sh/uv` and `astral-sh/ruff` keep a repo-root `AGENTS.md` with Rust-project conventions, and the ruff/ty repo ships an actual Claude Code plugin — install it once per checkout with `/plugin marketplace add ./.agents` then `/plugin install ty-skills@ruff-agent-skills` to get ty-specific development skills. Both projects point contributors at a shared org-wide `AI_POLICY.md` and will close PRs that don't follow it. Separately from that contributor-only tooling, Astral also ships an official plugin for people USING its tools in their own projects: `astral-sh/claude-code-plugins` bundles `/astral:uv`, `/astral:ruff`, and `/astral:ty` skills plus a ty language-server integration - install with `/plugin marketplace add astral-sh/claude-code-plugins` then `/plugin install astral@astral-sh` (https://github.com/astral-sh/claude-code-plugins).

**Pyrefly** (Meta's type checker) publishes an official `llms.txt` index at `https://pyrefly.org/llms.txt` — a table of contents listing roughly 40 official doc pages (installation, configuration, migrating from mypy/pyright, error suppressions, and more), so fetch it the same way as the uv/Ruff/ty index files above. It still has no official MCP server — only unrelated community wrappers (mcp-pyrefly, mcp-pyrefly-autotype) exist. It also publishes an official blog post, "Adding Pyrefly Type Checking to Your Agentic Loop," that gives a concrete pattern: add a line to your project's `AGENTS.md` requiring `pyrefly check` before a task is considered done, and/or wire a Claude Code Stop hook that runs it automatically. Treat this as a recipe to apply, not a file to fetch.

**pytest and Alembic** publish nothing official for agents as of this check — no `llms.txt` (checked both the docs subdomain and project root for each), no AGENTS.md convention in their source repos, and no official MCP server. Don't invent resources for these two; if an agent needs current pytest or Alembic API detail, fall back to Context7 or a direct docs fetch rather than assuming a first-party agent surface exists.
