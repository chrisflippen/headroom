<!-- freshness verified=2026-08-21 baseline=2026-09-04 -->
<!-- probe: create-astro | npm view create-astro version | 5.2.4 -->
<!-- probe: astro | npm view astro version | 7.3.1 -->
# Astro Scaffold-First Reference (verified 2026-08-21, round 3)

## Official setup commands

**Create a new project**
```
npm create astro@latest -- --yes
```
(pnpm: `pnpm create astro@latest -- --yes`, yarn: `yarn create astro --yes`, bun: `bun create astro@latest --yes` — bun is not full parity, see Choosing below)

This runs the official setup wizard and generates:
- `src/pages/` with a starter `index.astro`
- `public/` for static assets
- `astro.config.mjs` (the project's config file)
- `tsconfig.json` (extends an official Astro TypeScript preset)
- `package.json` with `dev`, `build`, `preview` scripts already wired
- a lockfile matching whichever package manager ran the command

**Humans at a terminal:** running `npm create astro@latest` without `--yes` launches the interactive Houston wizard, which prompts for project name, TypeScript strictness, dependency install, git init, and AI-agent-file creation — that's fine by hand. **Agent alternative:** add `--yes`/`-y` ("Skip all prompts by accepting defaults") to run unattended, or pass the specific flags instead: `--template`, `--install`/`--no-install`, `--git`/`--no-git`, `--no-ai`, `--skip-houston`. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md`, flags table.)

**Create a project with a starter template**
```
npm create astro@latest -- --template <example-name> --yes
```
Use an official example name or `<github-user>/<github-repo>` to start from a template instead of the blank starter. `--template` only sets the starter — the remaining prompts (install, git, TypeScript, AI files) still fire unless `--yes` is also passed. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md`.)

**Create a project with integrations pre-added**
```
npm create astro@latest -- --add react --add partytown --yes
```
Bundles integration setup into the same scaffold run instead of running `astro add` afterward. `--add` only preselects integrations — other prompts still need `--yes` to suppress. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md`.)

**Add an integration to an existing project**
```
npx astro add <integration-name> --yes
```
You can pass more than one at once: `npx astro add react sitemap partytown --yes`. This is the official, supported way to add React, Vue, Svelte, Solid.js, Preact, Alpine.js, the Cloudflare/Netlify/Node/Vercel adapters, Markdoc, MDX, Partytown, Sitemap, and any community package that opts in to this command. It installs the npm package(s), installs peer dependencies, and edits `astro.config.mjs` for you.

**Humans at a terminal:** without `--yes`, `astro add` shows a `Continue? (Y/n)` confirmation before installing/editing config. **Agent alternative:** `--yes` is documented as "Accept all prompts." A historical bug (GitHub issue #13399, fixed by PR #13426) let this prompt survive `--yes` for a non-official package on Astro v5.4.3 — the fix shipped, but smoke-test `--yes` on the pinned Astro version before trusting it blindly in an unattended pipeline. (Source: `docs.astro.build/en/reference/cli-reference/`; `github.com/withastro/astro/issues/13399`.)

**Upgrade Astro itself**
```
npx @astrojs/upgrade
```
The official upgrade tool for moving to a new Astro major/minor version — use this instead of manually bumping the version number in package.json. No documented interactive prompt to skip; this one already runs unattended. (Source: `unpkg.com/@astrojs/upgrade/README.md`.)

## Choosing

**Package manager for `create astro`.** npm, pnpm, and yarn run the same `create astro@latest` command with full parity. bun works via a dedicated recipe, but Astro's own docs call out rough edges, and `--add` is documented-broken with bun (`github.com/withastro/astro/issues/14833`, closed not-planned) — verify bun behavior before trusting it in an unattended agent scaffold, and prefer npm/pnpm/yarn for anything that pairs `create astro` with `--add`. (Source: `docs.astro.build/en/recipes/bun/`; `github.com/withastro/astro/issues/14833`; `docs.astro.build/en/reference/cli-reference/`.) Pick whichever your team already standardizes on — the generated project and lockfile match whichever one you ran. Don't mix them on one project; running `npm install` on a pnpm-scaffolded project leaves two lockfiles and a broken dependency tree.

**Blank starter vs. `--template`.** Use the blank starter (no `--template` flag) when you want the smallest possible project to build up yourself. Use `--template <example-name>` (an official example) or `--template <github-user>/<github-repo>` when a close-enough starting point already exists — it saves reconstructing routing, layout, and content-collection boilerplate by hand.

**Add integrations at scaffold time vs. after.** `create astro -- --add react --add partytown` bakes integration setup into the same run as project creation — fewer commands, one shot. Running `astro add <name>` afterward is the right call when you're integrating with an existing project, or adding something the original scaffold author didn't know they'd need yet. Both paths do the same thing under the hood (install package, wire peer deps, edit `astro.config`); the difference is just when you decide.

**Official adapter choice (Cloudflare / Netlify / Node / Vercel).** This is a hosting decision, not an Astro decision — pick the adapter matching wherever the site will actually deploy, then run `astro add <adapter-name>` to wire it in. Don't hand-install and hand-configure an adapter; `astro add` is what keeps the adapter's peer-dependency versions in sync with the installed Astro version.

**Build-time content collections vs. live (runtime) collections.** `src/content.config.ts` with `defineCollection()` + a `glob()`/`file()` loader is the default: content is read and typed at build time, which is what most blogs/docs sites want. `src/live.config.ts` with `defineLiveCollection()` (new in Astro 6+) is for content that has to be fetched fresh on every request — a live CMS preview, a frequently-changing API-backed collection — where build-time freshness isn't good enough. Reach for live collections only when build-time genuinely can't satisfy the requirement; they trade the build-time type/perf guarantees for request-time freshness.

**TypeScript preset: base vs. strict vs. strictest.** `astro/tsconfigs/base` is the minimum (modern JS support only). Astro's own docs recommend `strict` or `strictest` for any project that's actually writing TypeScript, not just using `.astro` files with JS. Pick `strict` as the default; reach for `strictest` only if the team already writes disciplined, fully-typed TS elsewhere.

**Upgrading: `@astrojs/upgrade` vs. a manual version bump.** Always use `npx @astrojs/upgrade` — it updates Astro and all installed official integrations together and is the only path that picks up the codemods/config migrations that ship with major version bumps (like the Astro 6 zod-import change). A manual `package.json` version bump skips those migrations silently.

## Never hand-write

- **`astro.config.mjs` / `astro.config.ts`** — the scaffold command creates the first version; `astro add` edits it when you install an integration. Do not type a fresh one from memory or copy one from an older Astro version into a newer project — config shape changes between majors: Astro 6 added new top-level keys like `fonts` and `security.csp`; Astro 7 added its own new top-level keys (`cache`, `routeRules`) and switched the default Markdown processor. (Source: `docs.astro.build/en/reference/configuration-reference/`; `astro.build/blog/astro-7/`.)
- **`tsconfig.json`** — generated by the wizard, extends `astro/tsconfigs/base`, `/strict`, or `/strictest`. Hand-editing the `extends` target instead of picking the right preset during setup is a common mistake.
- **`package.json`'s Astro-related dependencies and scripts** — written by `create astro` and `astro add`. Don't hand-copy dependency versions from another project; run the generators so peer dependencies land correctly.
- **`src/content.config.ts`** (Astro 5 and 6) or **`src/content/config.ts`** (older Astro 4 and earlier layout) — the file itself is written by the developer, but the import lines and loader wiring are version-specific and easy to get wrong from memory. This is historical context, kept because the same content collections may still be running on an older Astro major:
  - Astro 6: `import { z } from "astro/zod"`
  - Astro 5 and earlier: `import { z } from "astro:content"`
  Do not port one version's import style into the other.
- **`src/live.config.ts`** — new in Astro 6 for live (runtime) content collections, using `defineLiveCollection()`. This is a new file shape; don't improvise it from the build-time collections pattern.

## Thorough setup checklist

- [ ] Project created with `npm create astro@latest -- --yes` (or the pnpm/yarn/bun equivalent) — never assembled by hand
- [ ] `astro.config.mjs`/`.ts` present and matches what the scaffold + `astro add` runs actually produced
- [ ] `tsconfig.json` present, extending an official Astro preset
- [ ] Every integration (UI framework, adapter, MDX, sitemap, etc.) installed via `astro add <name> --yes`, never via manual `npm install` + hand-edited config array
- [ ] If the site uses content collections, `src/content.config.ts` exists, using `defineCollection()` with a `loader` (`glob()` or `file()` from `astro/loaders`) — not the deprecated `type: 'content'` pattern
- [ ] Zod import in content config matches the installed Astro major (`astro/zod` for Astro 6, `astro:content` for Astro 5)
- [ ] Exactly one lockfile exists, matching the package manager used to scaffold
- [ ] `package.json` has the standard `dev`/`build`/`preview`/`astro` scripts, unmodified from what the wizard generated
- [ ] Node 22+ is the runtime (Astro 6 requires it; Node 18/20 are no longer supported)
- [ ] Any Astro-version upgrade was done with `npx @astrojs/upgrade`, not a manual version bump
- [ ] The installed Astro major was checked (`astro --version` or `package.json`) before trusting any version-specific claim in this page — Astro moves fast enough that "current version" guidance goes stale within months

## Traps

**`create astro` and `astro add` are interactive by default — they will hang forever with no TTY.** Both show a wizard/confirmation flow (project name, TypeScript strictness, install, git init, AI-agent files for `create astro`; a `Continue? (Y/n)` prompt for `astro add`). An agent shell with no TTY needs `--yes`/`-y` explicitly, or it will sit waiting for input that never comes. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md` flags table.)

**`astro add --yes` has a documented history of not fully suppressing prompts for non-official packages.** GitHub issue #13399 (closed, fixed by PR #13426, filed against Astro v5.4.3) shows `astro add astro-compressor --yes` still printing a `Continue?` prompt because the package wasn't an official Astro integration. The fix shipped, but it's a reminder to smoke-test `--yes` behavior against the exact Astro version pinned in CI before trusting it unattended. (Source: `github.com/withastro/astro/issues/13399`.)

**Astro's major-version cadence is fast enough that "current version" guidance goes stale within months.** Astro 6 went stable in March 2026; Astro 7 replaced it as current in June 2026 — a 3-month gap. This page's own round-1 version, dated August 2026, ran into exactly that problem: it still called Astro 6 current after Astro 7 had already shipped. Anything that hardcodes "Astro N is current" needs an `astro --version` / `package.json` check before its version-specific claims (zod import path, config keys, Node minimum) are applied blindly. (Source: `astro.build/blog/astro-6/`, `astro.build/blog/astro-7/`.)

**The content-collections zod import silently breaks across majors instead of erroring clearly.** `import { z } from "astro:content"` (Astro 5 and earlier) vs. `import { z } from "astro/zod"` (Astro 6+) look interchangeable but aren't — copying a content config from an older-major project into a newer one produces confusing type errors rather than an obvious "wrong import" message. (Source: `docs.astro.build/en/guides/content-collections/`, `astro.build/blog/astro-6/`.)

**Astro 7's new Rust-based compiler stopped auto-correcting invalid markup.** The old Go compiler silently fixed unclosed tags and reordered/auto-closed invalid nesting; Astro 7's compiler now errors on the same markup (unclosed tags, unterminated attributes) and collapses inter-element whitespace to JSX conventions. A `.astro` file that built fine under Astro 6 can fail to compile after an in-place Astro 7 upgrade, purely from previously-tolerated markup. (Source: `astro.build/blog/astro-7/`.)

**`create-astro` now scaffolds AI-agent file(s) by default, which this kind of reference doc can miss.** The `--no-ai` flag ("Skip creating AI agent files") only makes sense if the default behavior creates them — an agent scaffolding a new project should decide deliberately whether it wants that default output or should suppress it with `--no-ai`, rather than assuming a blank slate. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md`.)

**Hand-writing `astro.config.mjs` from memory instead of running `astro add`.** This skips the peer-dependency install and often produces an integrations array that doesn't match what the installed package actually expects. (Source: `docs.astro.build/en/guides/integrations-guide/`.)

**Using the deprecated `type: 'content'` collection shape.** The Content Layer API's `loader: glob(...)` / `loader: file(...)` pattern is what current Astro docs treat as the supported path. (Source: `docs.astro.build/en/guides/content-collections/`.)

**Manually bumping the Astro version in `package.json` instead of running `npx @astrojs/upgrade`.** This misses the config migrations that come with major version bumps, like the zod-import change between Astro 5 and 6. (Source: `unpkg.com/@astrojs/upgrade/README.md`.)

**Assuming `astro add` covers every integration.** Some community packages aren't opted into the command; when it isn't supported, the fallback is documented manual installation, not silently faking the same effect by hand. (Source: `docs.astro.build/en/guides/integrations-guide/`.)

## AI and agent resources

Astro does not publish a working `llms.txt`/`llms-full.txt` (it shipped one briefly, then removed it). Instead it standardized on an official MCP server as the one blessed way for agents to get current Astro documentation, plus some CLI-level "agent awareness" baked into the `astro` command itself.

- **Astro Docs MCP server** — `https://mcp.docs.astro.build/mcp` (streamable HTTP; source: `github.com/withastro/docs-mcp`, official `withastro` org repo). Connect any MCP-capable agent to this before doing nontrivial Astro work — config syntax, integration setup, API details — instead of trusting training data, since Astro's APIs and integrations move fast. Example: `claude mcp add --transport http astro-docs https://mcp.docs.astro.build/mcp`.
- **"Building Astro sites with AI tools" guide** — `https://docs.astro.build/en/guides/build-with-ai/`. The official setup page for the MCP server across a growing list of AI tools (Claude Code, Cursor, VS Code, Windsurf, GitHub Copilot Coding Agent, etc. — 15 as of this check, but this list changes often), plus general tips for agent-driven Astro development. Worth a read once, at project setup time.
- **Agent-aware `astro dev`/`astro preview`** — documented at `https://docs.astro.build/en/reference/cli-reference/`. When Astro detects it's being launched by a coding agent, `astro dev` automatically runs as a detached background process instead of blocking the agent's terminal, writes a `.astro/dev.json` lock file (URL, port, PID), and adds `astro dev stop` / `astro dev status` / `astro dev logs` subcommands. An agent running the Astro CLI directly can rely on this instead of manually backgrounding the process, or pass `--background` explicitly. `astro preview` also takes `--ignore-lock` (added in Astro 7.3.0) so several preview servers can run at once on different ports — that's the flag to reach for when a Playwright or other E2E run needs more than one.
- **`AGENTS.md` in the Astro framework repo itself** — `github.com/withastro/astro/blob/main/AGENTS.md`. This is Astro's own contributor-facing agent file for people (and agents) working on the Astro framework's monorepo — not a template the `create astro` scaffolder currently drops into new user projects. Relevant only if an agent is contributing to Astro core itself, not to a site built with Astro.
- **`create-astro` scaffolds AI-agent file(s) into new end-user projects by default.** The official CLI flags table documents `--no-ai` as "Skip creating AI agent files," which only makes sense if a default `create astro@latest` run creates one (confirm the exact filename, e.g. `AGENTS.md`, with a real run before relying on it). Pass `--no-ai` to skip it if the project doesn't want it; a project already layering its own `CLAUDE.md` should check for the scaffolded file first rather than assuming a blank slate. (Source: `github.com/withastro/astro/blob/main/packages/create-astro/README.md`, CLI flags table.)
