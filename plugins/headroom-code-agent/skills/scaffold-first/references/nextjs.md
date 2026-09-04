<!-- freshness verified=2026-08-21 baseline=2026-09-04 -->
<!-- probe: create-next-app | npm view create-next-app version | 16.3.4 -->
<!-- probe: next | npm view next version | 16.3.4 -->
# Next.js Scaffold-First Reference (verified 2026-08-21 round-3, Next.js 16.3.2)

## Official setup commands

| Command | What it generates |
|---|---|
| `npx create-next-app@latest [name] --yes` (or `pnpm create next-app --yes`, `yarn create next-app --yes`, `bun create next-app --yes`) | Full new project, no prompts, using the **current recommended defaults**: TypeScript, ESLint, Tailwind CSS, App Router, Turbopack, import alias `@/*`, plus `AGENTS.md`/`CLAUDE.md`. Includes `app/` directory with `layout.tsx` + `page.tsx`, `public/`, `package.json` with `dev`/`build`/`start`/`lint` scripts, `tsconfig.json`, `next.config.ts` (or `.js`), `next-env.d.ts`, `.gitignore`, and a git repo init. |
| `npx create-next-app@latest [name] --ts --eslint --tailwind --app --src-dir --import-alias "@/*"` | Explicit-flag version of the same, if you need to override one thing (e.g. add `--src-dir`, or `--js` for JavaScript, `--biome` instead of ESLint, `--empty` for a bare project). Every choice the wizard would otherwise ask about is supplied as a flag, so this also runs with no prompts. |
| `npx next upgrade` (or `pnpm/yarn/bun` equivalents) | Upgrades `next`/`react`/`react-dom` in `package.json`, and refreshes the version-matched docs bundled at `node_modules/next/dist/docs/` that AI agents read. |
| `npx @next/codemod@canary next-lint-to-eslint-cli .` | Migrates a project still using the removed `next lint` command to plain ESLint CLI scripts in `package.json`. |

**Humans at a terminal:** running the bare command with no flags — `npx create-next-app@latest [project-name]` — opens a multi-step interactive wizard (project name, a "recommended defaults" yes/no, then individual TypeScript/linter/Tailwind/src-dir/App-Router/import-alias prompts if you say no). That's fine for a person at a keyboard. An agent with no TTY should always use `--yes` or the explicit-flag command above instead — the bare command will hang waiting for input that never comes (see Traps, below).

There is **no separate generator command** for ESLint or Tailwind outside of `create-next-app`'s own flags — if you're adding either to a project after the fact, you install the packages and copy the exact file contents from the current official docs page (linked below), not from memory. Tailwind v4 (the current default) has no `tailwindcss init` command; `tailwind.config.js` is gone by default.

## Choosing

**Bare `create-next-app` vs. `--yes` vs. explicit flags.** All three call the same tool, but they fit different situations. Run it with no flags when a person is sitting at the keyboard and should make each call (TypeScript? which linter? Tailwind? src/ folder?). Use `--yes` when an agent is bootstrapping a project and the current recommended defaults (TypeScript, ESLint, Tailwind, App Router, Turbopack, AGENTS.md) are fine as-is. Use the explicit-flag form when you need everything default except one or two things — say, Biome instead of ESLint, or a `src/` layout — since flags skip the prompt for exactly the choices you name and let the CLI fill in the rest.

**ESLint vs. Biome as the linter.** `create-next-app` now offers both as first-class options (`--eslint` / `--biome` / `--no-linter`). ESLint wins when you need its huge rule ecosystem or the team already standardized on it. Biome wins when you want one fast tool that lints and formats together and don't need ESLint-specific plugins — Next.js ships it with built-in Next.js/React rule support, not as a stripped-down alternative.

**Turbopack vs. Webpack.** Turbopack was stable but opt-in (`--turbo`) in Next.js 15; it became the default bundler for both `next dev` and `next build` in Next.js 16. Only reach for `--webpack` when a specific loader, plugin, or custom Webpack config genuinely has no Turbopack equivalent yet — check the Turbopack docs page first rather than assuming.

**Tailwind v4 (default) vs. the v3 guide.** `create-next-app --tailwind` installs Tailwind v4, which needs no config file — just a package install, a two-line `postcss.config.mjs`, and `@import "tailwindcss"` in your CSS. Fall back to the documented Tailwind v3 setup only if you need support for much older browsers than v4 targets; that's the one case Next.js's own docs still call out for v3.

**`next upgrade` vs. `npx @next/codemod upgrade`.** `next upgrade` (added in Next.js 16.1) is the short, low-ceremony version bump — good for routine "stay current" runs. `npx @next/codemod upgrade [revision]` is the heavier tool: it also runs the relevant codemods and the React 19 codemods, and lets you target a specific revision (patch/minor/major/tag/exact version) instead of just "latest." Reach for the codemod version when you're jumping a major version or expect breaking-change cleanup, not just a patch bump.

**Next.js MCP server vs. Vercel MCP.** These solve different problems and are normally used together, not as alternatives. The local `next-devtools-mcp` server only exists while `next dev` is running and only knows about the one project on your machine — routes, compile errors, dev-server logs. Vercel MCP is hosted, OAuth-protected, and knows about your whole Vercel account — deployments, production logs, analytics, and doc search — across every project. Use the local one for "why won't this route compile," the hosted one for "what does my last deploy's logs say."

**Skills vs. ad hoc agent work.** Official Next.js Skills (`npx skills add vercel/next.js --skill <name>`) are Vercel-authored, benchmarked, multi-step workflows for specific known migrations (e.g. adopting Cache Components). Use a Skill when your task matches one of the published workflows — it encodes checkpoints and verification steps Vercel already tuned. For anything that doesn't match a published Skill, let the agent work directly against the bundled docs instead of trying to force-fit a Skill that wasn't built for the job.

## Never hand-write

These files should come from `create-next-app` or another official generator/CLI, not be typed from memory or copied from an old project:

- **`next-env.d.ts`** — always machine-generated by Next.js; never edit it, and don't hand-recreate its contents.
- **`tsconfig.json`** — generated by `create-next-app`, or auto-created by Next.js the first time you rename a file to `.ts`/`.tsx` and run `next dev`. Add `paths`/`baseUrl` afterward, but don't hand-type the whole file from scratch.
- **`eslint.config.mjs`** — must use the current flat-config format and the current `eslint-config-next` exports (`eslint-config-next/core-web-vitals`, `eslint-config-next/typescript`). The legacy `.eslintrc.json` shape and the old `next lint` command are gone as of Next.js 16.0 — an agent working from older training data will get this wrong. Use the exact template from the current ESLint Plugin doc page, or let `create-next-app --eslint` generate it.
- **`postcss.config.mjs`** for Tailwind — current content is just the `@tailwindcss/postcss` plugin registration; don't recreate an old v3-style `postcss.config.js` with `tailwindcss` + `autoprefixer` plugins, that's stale.
- **`package.json` scripts block generated by create-next-app** (`dev`, `build`, `start`, `lint`) — don't add back a `next lint`-based script; current script is `"lint": "eslint"`.
- **`.next/` build output directory** — always generated, never hand-edited or committed.
- **AGENTS.md's Next.js-maintained block** — `next dev` writes and maintains a version-matched block in `AGENTS.md` pointing at the bundled docs; don't hand-author that block, though you can add your own project-specific content elsewhere in the file.

## Thorough setup checklist

A properly scaffolded Next.js App Router repo has, before feature work starts:

1. Project created via `create-next-app` (not hand-assembled), with TypeScript, ESLint (or Biome), Tailwind, and App Router chosen deliberately rather than left as unexamined defaults.
2. `app/layout.tsx` present as the root layout with `<html>`/`<body>`.
3. `tsconfig.json` present with the Next.js-recommended compiler options, plus any project-specific `paths` aliases layered on top, not replacing them.
4. `eslint.config.mjs` in flat-config format, importing `eslint-config-next/core-web-vitals` (and `eslint-config-next/typescript` for TS projects); `package.json` has `"lint": "eslint"` and `"lint:fix": "eslint --fix"` — not a `next lint` script.
5. If using Tailwind: `postcss.config.mjs` registers `@tailwindcss/postcss`, and `app/globals.css` (or `app/global.css`) has `@import "tailwindcss";` and is imported once from the root layout.
6. `next.config.ts` (TypeScript projects) or `next.config.js`/`.mjs` present — empty/minimal is fine; this file is meant to be hand-edited as you turn on specific documented options (e.g. `cacheComponents`, `typedRoutes`), unlike the generated files above.
7. Turbopack is the active bundler (the default as of Next.js 16) unless there's a specific documented reason to force `--webpack`.
8. `.gitignore` includes `.next/`, `node_modules/`, and env files, as generated by the CLI.
9. `AGENTS.md` present (and `CLAUDE.md` referencing it) so coding agents read version-matched docs instead of stale training knowledge — regenerate/refresh this on every `next upgrade`.
10. Node.js version on the machine is at least 20.9, the documented minimum.

## Traps

**Assuming `next build` still lints.** As of Next.js 16, `next build` no longer runs the linter automatically — an agent that treats a clean build as proof the code passes lint will ship unlinted code. Lint has to be a separate, explicit step. (Source: https://nextjs.org/docs/app/getting-started/installation, "Good to know: Starting with Next.js 16, `next build` no longer runs the linter automatically.")

**Hand-writing `eslint.config.mjs` from old training data.** `next lint` and the `next.config.js` `eslint` option were both removed in Next.js 16.0. An agent that reproduces the old `.eslintrc.json` shape or a `next lint` script from memory will generate a config the current toolchain doesn't recognize. (Source: https://nextjs.org/docs/app/api-reference/config/eslint, "`next lint` removal" callout and the v16.0.0 changelog row.)

**Reflexively running `npx tailwindcss init -p`.** That command and the `tailwind.config.js` file it produces are Tailwind v3 habits. Tailwind v4 — the `create-next-app --tailwind` default — needs no config file at all: just the package install, a two-line `postcss.config.mjs` registering `@tailwindcss/postcss`, and `@import "tailwindcss";` in the CSS. Running the v3 init command on a v4 project adds a file the framework won't read. (Source: https://nextjs.org/docs/app/getting-started/css, Tailwind CSS setup section.)

**Running bare `create-next-app` from an agent with no TTY.** Without `--yes` or a full set of explicit flags, `create-next-app` is a multi-step interactive wizard (project name, defaults Y/N, then individual TypeScript/linter/Tailwind/etc. prompts). An agent that shells out to the bare command will hang or fail waiting on input that never comes. (Source: https://nextjs.org/docs/app/api-reference/cli/create-next-app, prompt sequence shown under "Examples.")

**`AGENTS.md` auto-generation is version-gated.** Automatic `AGENTS.md`/`CLAUDE.md` generation on `next dev` (for existing, non-`create-next-app` projects) only happens on Next.js 16.3+, and only when an AI agent is detected and no managed block already exists. On 16.2 the docs are bundled but the file isn't auto-created — you add it by hand. On 16.1 and earlier, neither the bundled docs nor auto-generation exist; you need the legacy `npx @next/codemod@canary agents-md` command instead. An agent that assumes every Next.js 16 project already has a version-matched `AGENTS.md` can be working from nothing at all. (Source: https://nextjs.org/docs/app/guides/ai-agents, "Existing projects" and "For earlier versions" sections.)

**Two of the Next.js MCP server's tools are Turbopack-only.** `get_compilation_issues` and `compile_route` from `next-devtools-mcp` only work when the dev server is running on Turbopack. A project that's been forced onto `--webpack` (for a documented compatibility reason) silently loses those two tools. (Source: https://nextjs.org/docs/app/guides/mcp, "Available tools" list, both entries marked "Turbopack only.")

**`npx skills add` can stall an unattended agent without `-y`.** The skills CLI has interactive prompts as part of normal installation (e.g. picking which agent config to install into), and ships a documented `-y` flag specifically to skip them. Naming `--skill <name>` avoids the "which skill" prompt but not necessarily the rest. (Source: https://github.com/vercel-labs/skills README.)

**Hand-editing `next-env.d.ts` or committing changes to it.** It's fully machine-generated and gets overwritten, so any manual edits are silently lost. (Source: https://nextjs.org/docs/app/api-reference/config/typescript#next-envdts)

**Copying a `next.config.js` from an old project wholesale.** Old configs can carry options that have since moved, been renamed, or removed (e.g. old experimental flags). Start from the CLI-generated minimal file and add only the specific, currently-documented options you actually need. (Source: https://nextjs.org/docs/app/guides/upgrading/version-16#removals)

**Not running `next upgrade` before starting a new feature on an existing app.** Working from stale bundled docs or agent guidance that no longer matches the installed Next.js version leads an agent astray. (Source: https://nextjs.org/docs/app/getting-started/installation#upgrade-your-nextjs-app)

## AI and agent resources

Next.js (via Vercel) ships a genuinely first-party set of resources for coding agents, confirmed directly from nextjs.org and vercel.com as of August 2026 (Next.js 16.3.2):

- **llms.txt / llms-full.txt** — `nextjs.org/docs/llms.txt` is a linked index of every doc page; `nextjs.org/docs/llms-full.txt` bundles the entire doc set into one markdown file. Pull these in when an agent needs a map of the docs or the whole corpus at once.

- **AGENTS.md / CLAUDE.md, scaffolded automatically** — `create-next-app` writes `AGENTS.md` (plus a `CLAUDE.md` that includes it) into every new project. On Next.js 16.3+, running `next dev` in an existing project will add the same file if none exists. The file's job is simple: tell the agent this Next.js version may not match its training data, and to read the docs bundled in `node_modules/next/dist/docs/` before writing code. Keep this file in place — it's version-matched and self-updating.

- **Bundled docs in the package itself** — `node_modules/next/dist/docs/` mirrors the full docs site and always matches the installed Next.js version, no network call required. Vercel's own evals found this beat Skills-based retrieval (100% vs. 79% pass rate), so an agent should default to reading here first.

- **Markdown over the network** — append `.md` to any `nextjs.org/docs` URL (or send `Accept: text/markdown`) for a plain-text version, including the per-error pages under `/docs/messages/*` that build and runtime errors link to.

- **Next.js MCP server (`next-devtools-mcp`)** — an official local MCP server that connects to a running `next dev` instance at its built-in `/_next/mcp` endpoint. Install via `.mcp.json` and it gives an agent live tools: `get_errors`, `get_logs`, `get_routes`, `get_compilation_issues`, `compile_route`, and more. Use this instead of asking the user to paste terminal output.

- **Vercel MCP (`https://mcp.vercel.com`)** — Vercel's official hosted, OAuth-protected MCP server for the whole platform: search docs, inspect deployments and logs, query analytics. Officially supported by Claude Code, Claude.ai/Desktop, Codex CLI, Cursor, and others — add it once per client.

- **Official Next.js Skills** — installable, structured multi-step workflows (`npx skills add vercel/next.js --skill <name> -y` — the `-y` flag skips the CLI's interactive prompts, such as which agent config to install into), such as an edit-and-verify dev loop or a guided Cache Components migration. Reach for these when the task is a workflow, not a lookup.

No unofficial or third-party tools are listed here — everything above was fetched directly from nextjs.org or vercel.com.
