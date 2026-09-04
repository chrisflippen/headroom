<!-- freshness verified=2026-08-21 baseline=2026-08-30 -->
# GitHub Actions CI setup — plain-language reference (verified 2026-08-21, round 4)

This covers how to set up Continuous Integration (CI) with GitHub Actions the way GitHub and the major tool vendors (Astral for `uv`, the Node.js team for `setup-node`, pnpm) actually document it, as of August 2026.

## Official setup commands

There is no single CLI command that "scaffolds" a GitHub Actions workflow the way `npm init` scaffolds a project. GitHub Actions workflows are YAML files, and the *official* way to generate one is:

| What you want | Official command / mechanism | What it generates |
|---|---|---|
| A starting CI workflow file (Node.js, Python, etc.) | GitHub web UI: repo → **Actions** tab → **New workflow** → pick a template → **Configure** → **Start commit** — humans at a terminal only, see the note below for the agent-usable alternative | `.github/workflows/<name>.yml`, pre-filled from GitHub's own [`actions/starter-workflows`](https://github.com/actions/starter-workflows) repo |
| A Python project with a lockfile for `uv` | `uv init` (new project) and `uv add <package>` / `uv sync` (existing project) | `pyproject.toml` and `uv.lock` |
| A Node project with a pnpm lockfile | `pnpm install` (after `pnpm init` for a new `package.json`) | `pnpm-lock.yaml` |
| A Node project with an npm lockfile | `npm install` (after `npm init -y` for a new `package.json`) | `package-lock.json` |

**Humans at a terminal:** the "New workflow" wizard is a browser flow — an agent can't drive it headlessly. Documented agent-usable alternative: pull the template file directly from the public `actions/starter-workflows` repo (e.g. `gh api repos/actions/starter-workflows/contents/ci/<template>.yml`, or a raw.githubusercontent.com fetch) and commit it with git/gh — that's the same source content the wizard copies from. (Source: [`actions/starter-workflows`](https://github.com/actions/starter-workflows).)

**On `npm init`:** run it as `npm init -y` (or `--yes`). Plain `npm init` launches an interactive questionnaire; an unattended agent run will hang waiting for input that never comes. (Source: [docs.npmjs.com/cli/v12/commands/npm-init](https://docs.npmjs.com/cli/v12/commands/npm-init).)

Because there's no CLI generator for the workflow YAML itself, "scaffold-first" here means: **start from a GitHub starter workflow or the tool's own published example**, don't invent the YAML from memory. The closest things to canonical machine-generated CI starting points are the files in `actions/starter-workflows` and the copy-paste examples in each action's own README (`astral-sh/setup-uv`, `actions/setup-node`, `pnpm/action-setup` or `pnpm/setup`).

## Choosing

**Starting the workflow file: web wizard vs. pulling the template yourself.** GitHub's own "official" path is the Actions tab wizard, but that's a browser flow a coding agent can't drive. Since the wizard just copies files out of the public `actions/starter-workflows` repo, an agent should fetch that same file directly (`gh api` or a raw file fetch) and commit it. Use the wizard when a human is doing the setup by hand; have the agent go straight to the source repo otherwise.

**pnpm setup: one action or two.** If the project is on pnpm 11 or newer (the current stable line), use the newer `pnpm/setup` action by itself — it installs both pnpm and the JS runtime in one step and is what pnpm's own org now points people to. Only fall back to the older two-step pattern (`pnpm/action-setup` running before `actions/setup-node`) if the project is pinned to pnpm 10 or older, where `pnpm/setup` doesn't work.

**Yarn's lockfile flag: `--frozen-lockfile` vs `--immutable`.** Both stop Yarn from silently rewriting the lockfile in CI, but they're not equally future-proof. `--frozen-lockfile` is a Yarn Classic (v1) holdover that Yarn Berry keeps only as a backward-compatible alias slated for removal. Use `--immutable` for any project on modern Yarn; keep `--frozen-lockfile` only for a project still genuinely on Yarn 1.

**Tag pin vs. SHA pin for third-party actions.** A major-version tag (`@v4`) is easier to read and lets Dependabot alert you automatically, but the repo owner can move it. A full 40-character commit SHA is the only pin GitHub's own docs call immutable — nobody can silently swap what runs. The real-world cost of SHA pinning is that Dependabot's security alerts won't fire on a bare SHA; adding a trailing `# vX.Y.Z` comment restores Dependabot's ability to track and bump it. Pin to SHA-with-comment whenever the org can enforce or benefit from that immutability guarantee (and GitHub now offers an org setting, "Require actions to be pinned to a full-length commit SHA," that can make this mandatory); a tag pin is a reasonable lighter-weight choice for a low-stakes personal repo.

**Checking CI status: GitHub's MCP server vs. `gh-aw`.** These solve different problems. The official `github/github-mcp-server`'s `actions` toolset is for an agent that needs to *read* CI — list runs, pull job logs, filter to failures — while doing some other task. `github/gh-aw` is for when the goal is to *build* an AI-driven workflow itself (triage, review, failure investigation running as a scheduled or triggered Action). Reach for the MCP server when CI status is an input to your task; reach for `gh-aw` when CI automation is the deliverable.

## Never hand-write

These files are produced by a tool, not typed by hand:

- **`uv.lock`** — written by `uv sync`, `uv add`, `uv lock`. Never edit by hand; regenerate it.
- **`pnpm-lock.yaml`** — written by `pnpm install`. Never edit by hand.
- **`package-lock.json`** — written by `npm install`. Never edit by hand.
- **`yarn.lock`** — written by `yarn install`. Never edit by hand.
- Any workflow file you got by clicking "Configure" on a GitHub starter workflow should be treated as generated — customize the run steps, but don't re-derive the trigger/permissions boilerplate from memory next time; go back to the template.

## Thorough setup checklist

A properly set-up repo has all of this before feature work starts:

1. A committed lockfile for the package manager in use (`uv.lock`, `pnpm-lock.yaml`, `package-lock.json`, or `yarn.lock`) — CI installs *from* this file, it never resolves fresh.
2. A CI workflow that installs with a lockfile-enforcing flag, not a plain install:
   - Python/uv: `uv sync --locked` (fails the build if the lockfile is out of date instead of silently updating it)
   - pnpm: `pnpm install --frozen-lockfile` (pnpm ≥ 6.10 does this automatically on CI, but pin it explicitly)
   - npm: `npm ci` (not `npm install`)
   - Yarn: `yarn install --immutable` (Yarn Berry's current flag; `--frozen-lockfile` still works but is a deprecated alias, kept only for Yarn Classic/v1 projects that haven't migrated)
3. The language/tool setup action pinned to a specific major version tag at minimum (e.g. `actions/setup-node@v7`), ideally to a full commit SHA with the version as a trailing comment. Note: `astral-sh/setup-uv` doesn't offer this option — unlike `setup-node`, it stopped publishing bare major-version tags starting at v8, so pin it to a full version instead (e.g. `astral-sh/setup-uv@v10.0.1`) or to a SHA.
4. Dependency caching turned on through the setup action's built-in cache support, not a hand-rolled `actions/cache` block, when the setup action offers one:
   - `actions/setup-node` — `with: cache: 'npm' | 'yarn' | 'pnpm'`
   - `astral-sh/setup-uv` — `with: enable-cache: true` (default is `"auto"`, which caches when it detects a CI environment)
5. Every third-party action (anything not published under `actions/` or `github/`) pinned to a full 40-character commit SHA, with the human-readable version as a trailing `# vX.Y.Z` comment — not a floating tag like `@v4`.
6. For pnpm: on pnpm 11 and newer (the current stable line), use `pnpm/setup` by itself — it installs pnpm and the JS runtime in one step, replacing the two-action pattern. Only on pnpm 10 or older, run `pnpm/action-setup` *before* `actions/setup-node` so `setup-node`'s pnpm cache can find pnpm.
7. A `.python-version` file (for Python/uv projects) so the Python version is pinned the same way locally and in CI, rather than hard-coded only inside the workflow YAML.
8. If the org has adopted GitHub's action-pinning policy enforcement (an org setting, GA since August 2025), confirm the workflow already satisfies it — unpinned actions will hard-fail rather than warn.

## Traps

**A floating major-version tag looks pinned but isn't.** In a recorded test run (2026-08-21), an agent pinned a third-party action with `@v5` — a floating major tag — believing that satisfied "pin your actions." The standard requires a full version or a full commit SHA; a major tag can be repointed by whoever controls that repo at any time. GitHub's own docs are explicit that SHA pinning is "the only way to use an action as an immutable release" (recorded test run 2026-08-21; GitHub Actions docs).

**SHA-pinning an action makes it invisible to Dependabot's security alerts unless you add the version comment.** GitHub's own hardening guide says plainly: "Dependabot only creates alerts for vulnerable actions that use semantic versioning and will not create alerts for actions pinned to SHA values." The fix documented alongside it is to keep a trailing `# <tag or link>` comment on the same line, which lets Dependabot version updates still track and bump the SHA even though alerts don't cover it. This needs to be a deliberate, periodic bump — not pin once and forget. (Source: [docs.github.com — Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions).)

**A plain `npm init` will hang an unattended agent.** It launches an interactive questionnaire by default; only `-y`/`--yes` skips it. A CI script or agent that calls bare `npm init` before `npm install` will stall waiting for input that never comes. (Source: [docs.npmjs.com/cli/v12/commands/npm-init](https://docs.npmjs.com/cli/v12/commands/npm-init).)

**Yarn's `--frozen-lockfile` is a fading alias, not the current flag.** Yarn's own CLI docs say it's kept "for backward compatibility" and "will be removed in a later release," with `--immutable` as the documented replacement. A workflow that hard-codes `--frozen-lockfile` today works, but is building on a flag Yarn has already flagged for removal. (Source: [yarnpkg.com/cli/install](https://yarnpkg.com/cli/install).)

**The pnpm + `actions/setup-node` ordering trap has a newer twist.** Running `pnpm/action-setup` before `actions/setup-node` is still the right fix if you're on that older two-action pattern — get the order wrong and `setup-node`'s `cache: 'pnpm'` can't find pnpm. But pnpm's org has since shipped `pnpm/setup`, which pnpm 11+ projects should use instead of the two-action combo entirely, so the ordering rule only matters for pnpm 10 and older. (Sources: [github.com/pnpm/action-setup](https://github.com/pnpm/action-setup) README banner; [github.com/pnpm/setup](https://github.com/pnpm/setup) README.)

**Hand-typing a workflow YAML from memory instead of starting from a template.** Memory drifts — wrong input names, wrong default branch, stale action versions — while GitHub's starter workflows and each action's own README example don't (see Official setup commands, above; [github.com/actions/starter-workflows](https://github.com/actions/starter-workflows)).

**Skipping the lockfile-enforcing flag.** Running `npm install`, `pnpm install`, or `uv sync` without the flag that makes CI fail on a stale lockfile (`npm ci`, `pnpm install --frozen-lockfile`, `uv sync --locked`, `yarn install --immutable`) lets CI silently pass with a lockfile that doesn't match `package.json`/`pyproject.toml` — which then breaks a teammate's local install (see Thorough setup checklist, item 2). Each flag is documented to fail hard on a mismatch: `npm ci` ([docs.npmjs.com/cli/v12/commands/npm-ci](https://docs.npmjs.com/cli/v12/commands/npm-ci)), `uv sync --locked` ([docs.astral.sh/uv/reference/cli/#uv-sync](https://docs.astral.sh/uv/reference/cli/#uv-sync)), `pnpm install --frozen-lockfile` ([pnpm.io/cli/install](https://pnpm.io/cli/install)), `yarn install --immutable` ([yarnpkg.com/cli/install](https://yarnpkg.com/cli/install)).

**Hand-editing a lockfile** (`uv.lock`, `pnpm-lock.yaml`, `package-lock.json`) to "fix" a version conflict instead of re-running the tool. This produces a lockfile the tool itself will reject or silently regenerate differently (see Never hand-write, above; for `uv.lock` specifically, [docs.astral.sh/uv/concepts/projects/layout](https://docs.astral.sh/uv/concepts/projects/layout/) documents it as managed by uv and not meant for manual edits).

**Rolling a manual `actions/cache` block** for npm/yarn/pnpm/uv dependencies instead of using the setup action's built-in `cache:` / `enable-cache:` input, which already knows the right cache key and restore-key strategy for that package manager (see Thorough setup checklist, item 4; [github.com/actions/setup-node — caching](https://github.com/actions/setup-node#caching-global-packages-data), [github.com/astral-sh/setup-uv — caching](https://github.com/astral-sh/setup-uv#caching)).

## AI and agent resources

GitHub Actions itself does not ship a dedicated `llms.txt` or a standalone "Actions MCP server." What's official is folded into GitHub's platform-wide agent tooling:

- **GitHub's official MCP server (`github/github-mcp-server`)** — includes an `actions` toolset for workflow runs, jobs, artifacts, dispatching runs, and pulling job logs (with a failed-jobs filter). Connect this instead of scripting `gh` calls or scraping the Actions UI when an agent needs to check CI status or read failure logs.

- **GitHub Docs `llms.txt`** (`docs.github.com/llms.txt`) — an official index for the whole docs site, including Actions, plus a documented Article API that returns clean Markdown for any doc page. GitHub calls this "the preferred way for LLMs and automated tools to access GitHub documentation." Fetch it before writing or debugging workflow YAML so the syntax reference comes from GitHub's own current docs, not memory.

- **GitHub Agentic Workflows (`github/gh-aw`)** — an official, actively maintained project (now under the main `github` org) for writing AI-driven repo automation as Markdown files that compile into real Actions workflows. It supports Claude Code and other agents as engines and has its own guide for wiring in MCP servers. Reach for this when the goal is an AI-driven CI step (triage, review, failure investigation) rather than a conventional build/test job.

No dedicated Actions-only `llms.txt` or agent file convention (no Actions-specific AGENTS.md pattern) was found as of August 2026 — a community request for a purpose-built Actions MCP server is still open, so the `actions` toolset inside the general GitHub MCP server is the closest official equivalent.
