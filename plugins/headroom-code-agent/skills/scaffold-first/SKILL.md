---
name: scaffold-first
description: Repo and tooling setup standard for ALL ecosystems (Python/uv, JS/TS, SvelteKit, Next.js, TanStack Start, React Router, Angular, SolidStart, Qwik, Preact, Lit, Astro, Vue, Nuxt, Expo, React Native, Flutter, Kotlin Multiplatform, Electron and Tauri desktop apps, Swift, Rust, Go, Hono/Fastify/NestJS servers, Rails, Laravel, Spring Boot, ASP.NET Core, GitHub, CI, Docker, ML pipelines, scrapers, FastAPI/Django, codegen libraries, UI components via shadcn/shadcn-svelte), plus shipping (installers, code signing, notarization, licensing), cloud setup (Railway, GCP, Firebase), the freshness check, and the stack-choosing intake interview for new app ideas. Use whenever creating a repository, starting a project or app idea, picking a framework for a new product, adding a framework/library/tool that has any config file, adding a UI component, packaging or signing an app for distribution, choosing a license, deploying to cloud hosting, or setting up linting, type checking, formatting, testing, migrations, CI, containers, ML experiment tracking, data versioning, or scraping projects. Rule - run the official init/scaffold command through the uv spine for Python, never hand-write a config or a UI component from memory.
---

# Scaffold First: the repo setup standard

Standing rule (Christopher, 2026-08-21, global — all projects, all ecosystems).
A hand-written config is a guess from stale training data. Guesses caused
cascading failures (the pyrefly incident). Official scaffolders encode the
tool authors' current best practice, and that practice moves fast: ESLint 10
dropped every old config format in August 2026, Next.js 16 removed `next
lint`, Tailwind v4 needs no config file at all. Your memory is behind on
purpose-built details like these; the generators are not.

A PreToolUse guard (`~/.claude/hooks/scaffold-guard.py` plus Haiku agent
hooks in `~/.claude/settings.json`) enforces the common cases mechanically.
This skill is the full standard.

## The existing repos are scope input, never practice input

Christopher's repositories tell you WHAT this standard must cover — the
frameworks and libraries actually in use. They never tell you HOW anything
should be set up: their current state is what triggered this standard in
the first place. Provider documentation is the only authority on practice;
a pattern found in an existing repo is a candidate for auditing against
that authority, not evidence for it.

## Versions: never from memory, always latest

A version number recalled from training data is wrong by default — including
in passing thoughts, fixtures, and examples. Never type one. Resolve the
current latest through the tool itself at the moment of use (`uv python
list`, `npm view <pkg> version`, the provider's release page) and target
the most modern latest major version of every language, framework, and
package unless Christopher rules otherwise for a specific case. Pinning to
an older version is a decision he makes, never a default you inherit.

## Freshness — check before you trust a page

Every reference page opens with a machine-readable freshness block
(`verified` date, `baseline` date, version probes). Before relying on a
page, run the quick check for it:

```bash
python3 ~/.claude/skills/scaffold-first/freshness.py --quick <page-name>
```

Exit 0 = trust the page. Exit 1 = it's stale or a tool has moved — verify
the drifted commands against current docs before following them, and
update the page (and its block) with what you find. Exit 2 = the page is
missing its block or a probe broke — fix that first. A weekly scheduled
sweep runs `--sweep` (all probes) and files drift into the Linear project
"Setup Standard — One-Stop Shop"; never re-scaffold everything just
because a probe moved — re-verify the affected page only. New pages MUST
start with a freshness block: probes capture the page's anchor tools with
the live lookup command and the value seen at verification.

## The procedure (every tool, listed below or not)

-1. **For a NEW application whose stack isn't already ruled, run the intake
   interview first** (`references/choosing-a-stack.md`): a staged
   AskUserQuestion flow that gathers what the idea is and matches each
   surface (web, mobile, desktop, backend, ML, docs) to a stack — only
   stacks with a verified reference page may be recommended. Skip it when
   the stack is already decided or the work is inside an existing repo.
0. **For a whole application (not just one tool), check templates before
   composing.** A vetted template hands you the entire proven setup in one
   generate step — house shapes first (`references/template-catalog.md`
   points to the house-templates repo), then the catalog's admitted
   third-party picks (e.g. the full-stack FastAPI template for a
   FastAPI+frontend+DB app). Assembling an app from individual scaffolds
   is the fallback when no vetted template fits, not the default.
1. **Find the official init/scaffold/migrate command first.** Almost every
   modern tool ships one. If one exists, run it, then edit its output.
2. **Verify the command against current docs before running it** (Context7
   or the tool's docs) for anything not in the reference pages below.
3. **Never hand-edit machine-generated files** — lockfiles, resolved-
   dependency files, generated output directories, generated type stubs.
   Regenerate with the owning tool.
4. **After scaffolding, review and tune.** The generated file is the start,
   not the finish. Editing a generated config is expected.
5. **If no official scaffolder truly fits**, say so with the reason in your
   response, then create the file via Bash prefixed with `SCAFFOLD_OK=1 `.
   This applies ONLY to files with no generator at all: the guard hard-blocks
   `SCAFFOLD_OK` on any file from its generator-owned list, and reaching for
   it under time pressure instead of running the generator is itself a
   violation — the official command is nearly always faster anyway.

## Ecosystem references — read before setup work in that ecosystem

Each page carries the verified commands (August 2026), the never-hand-write
list, a thorough-setup checklist, and the mistakes agents actually make:

- `references/python-uv.md` — uv, pyrefly, ruff, pytest, pre-commit, alembic
- `references/js-ts-core.md` — pnpm/npm, tsconfig, ESLint 10, Biome, Vitest, Playwright
- `references/sveltekit.md` — the `sv` CLI: create, add-ons, adapters, sync
- `references/nextjs.md` — create-next-app, Next 16 realities (no `next lint`, Tailwind v4)
- `references/astro.md` — create astro, astro add, upgrade codemod
- `references/swift.md` — swift package init types, swift-format, what Xcode owns
- `references/rust.md` — cargo new/init/add/remove, edition 2024, lockfile policy
- `references/github-repo.md` — gh repo create, rulesets (via `gh api` — `gh ruleset create` does not exist), labels, Renovate onboarding
- `references/ci-github-actions.md` — starter workflows, lockfile installs, SHA pinning
- `references/docker.md` — docker init, uv's official Docker pattern, compose
- `references/ml-pipelines.md` — Kedro, ZenML, DVC, Dagster, MLflow, W&B, HF, NeMo/torch via uv; notebook hygiene; live CUDA resolution
- `references/scrapers.md` — Scrapy/Crawlee/Playwright scaffolds via uv, plus provider-documented throttle/retry/backoff settings
- `references/python-web.md` — FastAPI and Django through the uv spine, with their documented lint/type/test wiring
- `references/js-servers.md` — Hono, Fastify, and NestJS servers: non-interactive creators, boot checks, Nest 12's Vitest+oxlint reality, the no-tests Hono template trap
- `references/go.md` — Go toolchain from the go.dev feed, go mod init/get/tidy, stdlib server + chi, the Homebrew-shadowing trap
- `references/react-fullstack.md` — TanStack Start (via the unified @tanstack/cli — create-start is deprecated) and React Router v8 framework mode; the failing-out-of-the-box Biome check trap
- `references/solid-qwik.md` — SolidStart (pin every creator axis or it silently creates nothing) and Qwik City (the `base` template is gone; `empty` is the app starter)
- `references/preact-lit.md` — Preact via create-vite (create-preact cannot run unattended) and Lit's starter repos with the Node-26 fixes they need out of the box
- `references/angular.md` — Angular via ng new --defaults; Angular 22 tests are Vitest (Karma flags now mean something else), dev server is Vite
- `references/rails-laravel.md` — Rails (works on Ruby 4; vendor the bundle on this machine) and Laravel (composer create-project runs migrations itself; generates AGENTS.md/CLAUDE.md)
- `references/spring-dotnet.md` — Spring Boot via the Initializr REST API (Boot 4 is current) and ASP.NET Core via dotnet-install.sh + dotnet new (.NET 10 LTS)
- `references/shipping-desktop-mobile.md` — installers and store builds: Tauri dmg, Forge zip, Flutter --no-codesign, the ad-hoc signature trap, and where the Apple credential gate sits
- `references/licensing.md` — LICENSE generation via the GitHub licenses API and dependency-license audits per ecosystem (npm, uv, Go)
- `references/cloud-railway.md` — Railway via the authenticated connector (reads free, billable moves gated); the two-tool-surfaces auth trap
- `references/cloud-gcp.md` — gcloud state on this machine, reads vs gated mutations, the Homebrew no-self-update trap, the loaded default project
- `references/cloud-firebase.md` — the Firebase MCP plugin as the unattended init path, the offline emulator loop with demo- project ids, deploys gated
- `references/js-web-extended.md` — Vue, Nuxt, and Expo creators (Svelte/Next/Astro have their own pages)
- `references/electron.md` — Electron desktop apps: Forge's create-electron-app vs electron-vite's creator, the Playwright boot check, and the Node-26 silent-extraction traps
- `references/tauri.md` — Tauri 2 desktop apps: create-tauri-app (18 templates, truly non-interactive), the macOS AX-read boot check, no tauri-driver on macOS
- `references/flutter.md` — Flutter: SDK install from the official release feed, flutter create, the simulator boot check with screenshot, the DEVELOPER_DIR beta-Xcode workaround
- `references/kotlin-multiplatform.md` — Kotlin Multiplatform / Compose: the kmp.jetbrains.com wizard's curl-able endpoint, Compose Desktop boot via Gradle, jvmTest naming trap
- `references/react-native.md` — bare React Native: the Expo-first ruling, community CLI init, the UTF-8 CocoaPods crash, the Metro red-screen trap, Xcode 27's DeviceHub
- `references/choosing-a-stack.md` — the intake interview: staged AskUserQuestion rounds that match each surface of a new idea to a verified stack
- `references/libraries-codegen.md` — API-client/type generators, Prisma, Storybook, msw, husky, turbo — generated output is machine-owned
- `references/shadcn.md` — the `shadcn` (React) and `shadcn-svelte` CLIs for UI components; includes wiring the shadcn MCP server into Claude Code so components are added via tool call, not just shell-out
- `references/generators.md` — generator frameworks (Hygen, Plop, Copier, Cookiecutter, Nx) and spec-to-code tools (AsyncAPI Generator) — define boilerplate once, stamp it out, never hand-copy it
- `references/template-catalog.md` — third-party templates vetted by generating from each and grading the output; admitted by proof, rejected with evidence — consult before trusting ANY community template, famous or not
- `references/docs-mintlify.md` — Mintlify documentation sites: the `mint` CLI (npm package `mint`, not `mintlify`), unattended scaffolding, docs.json, CI checks, custom docs subdomains, and their agent-facing resources

## Use the ecosystems' own agent-facing resources

Most of these projects now publish resources built specifically for coding
agents, and each reference page ends with an "AI and agent resources"
section listing the verified ones. Follow it: fetch the project's
`llms.txt` (uv, ruff, Svelte, Next.js, Astro, Vitest, and others publish
one) before nontrivial work so you navigate current docs instead of
memory; prefer a project's official MCP server (Svelte, Playwright,
GitHub, Docker ship one) over guessing APIs; and honor agent-instruction
files — if a scaffolder creates or maintains AGENTS.md / CLAUDE.md (as
create-next-app does), keep them, don't delete or overwrite them. Never
substitute a third-party wrapper for an official resource.

## Tools with NO official generator — hand-writing is correct there

Do not invent init commands, and do not treat these as violations: ruff and
pytest config blocks in pyproject.toml, rustfmt.toml, clippy.toml, a Rust
workspace root Cargo.toml, a base vitest.config.ts, and GitHub Actions
workflow YAML (start from the official starter templates, but the YAML is
yours to write). When you hand-write one of these, follow the reference
page's example shape and current docs, not memory.

## What a thoroughly set-up repo has

Before feature work starts: scaffolded by the official tool (structure,
.gitignore, README stub included), runtime version pinned, lockfile
committed, formatter + linter + type checker installed through official
paths and wired into pre-commit, CI installing from the committed lockfile
and running the same checks, dependency updates automated (Renovate or
Dependabot), issue/PR templates and branch rulesets in place, README saying
what the repo is and how to run it. The per-ecosystem checklists make this
concrete. If an existing repo is missing pieces, surface the gap rather
than silently working around it.
