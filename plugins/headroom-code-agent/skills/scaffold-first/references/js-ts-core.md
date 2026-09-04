<!-- freshness verified=2026-09-03 baseline=2026-08-30 -->
<!-- probe: typescript | npm view typescript version | 7.0.2 -->
<!-- probe: eslint | npm view eslint version | 10.9.1 -->
<!-- probe: vitest | npm view vitest version | 5.0.0 -->
<!-- 2026-09-03 re-verify: Vitest 5.0.0 is current. The commands on this page are unchanged (npm i -D vitest; hand-written vitest.config.ts with defineConfig). Vitest 5 requires Node >= 22.12 and Vite >= 6.4; clearMocks now defaults to true; test.sequential/describe.sequential removed (use concurrent: false); unawaited async assertions fail tests. Source: vitest.dev/guide/migration. -->
# Core JavaScript/TypeScript Tooling — Setup Reference (verified 2026-08-21, round 8)

This page covers the tools every JS/TS project needs before any framework-specific work starts: package init, TypeScript, linting/formatting, and testing. It does not cover framework scaffolds (Next.js, Vite app templates, etc.) — those are a separate ecosystem.

## Official setup commands

Run these in order. Each one generates a file — don't type that file by hand first and run the command "to check."

1. **Project init — package.json**
   ```
   pnpm init
   ```
   or, if the project is staying on npm:
   ```
   npm init -y
   ```
   Creates `package.json` with the basic fields. pnpm is the faster, more disk-efficient option and the one worth defaulting to for new work in 2026; npm is still fine, it's just slower and heavier on disk for the same install.

2. **TypeScript — tsconfig.json**
   ```
   npm i -D typescript
   npx tsc --init
   ```
   Generates `tsconfig.json` with the current TypeScript defaults. TypeScript 7.0 (the Go-native compiler) reached general availability on July 8, 2026, and is what a plain `npm i -D typescript` installs today (source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/). The generated file already turns on strict mode, ESM module resolution, and a modern target — it is not the loose 2015-era default anymore. Edit compiler options after generating; don't write the file from a template in your head.

   TypeScript 7.0 ships without a stable programmatic API. If the project uses a Volar-based framework toolchain (Vue, Astro, Svelte, MDX), deliberately pin to TypeScript 6.x instead of installing 7.0 — those tools can't run against 7.0 yet. If the project uses typescript-eslint specifically, install TypeScript 7.0 with two aliased devDependencies instead of a plain install: `"typescript": "npm:@typescript/typescript6@^6.0.2"` — this is what satisfies typescript-eslint's peer dependency, since peer dependencies resolve against the literal package name `typescript` — plus `"@typescript/native": "npm:typescript@^7.0.2"`, which is what keeps a working, fast `tsc` on the real 7.0 compiler (aliasing `typescript` alone leaves you with only a `tsc6` executable, not `tsc`) (source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/, which recommends achieving this "via npm aliases," notes that doing so "will leave you only with a `tsc6` executable," and says "To get 7.0's `tsc`, you can add another alias for TypeScript 7 and `npx tsc` will just work with 7.0"). The TypeScript team says a new programmatic API is coming in 7.1 (source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/).

3. **ESLint — eslint.config.js / eslint.config.mjs**
   ```
   yes n | npm init @eslint/config@latest -- --config <shareable-config-package-name>
   ```
   Passing `--config` (e.g. `--config eslint-config-airbnb`) only skips the first prompt — which shareable config to use. A second, unconditional prompt always follows afterward, asking whether to install that config's dependencies now, and there is no flag that skips it. Piping a continuous stream to stdin (`yes n | ...` to decline auto-install and add the dependencies yourself, or `yes y | ...` to let it install them) gets a clean non-interactive run; running the bare `--config` command with closed stdin crashes instead of completing, rather than hanging cleanly. A single piped line (`printf "n\n" | ...`) is not a reliable substitute — it races against when the prompt's keypress listener attaches, so in 8 fresh-directory trials it crashed with `npm error code 1` / `✖ Operation canceled` (no file written) 5 times out of 8, and adding a second piped line still failed 3/8. (Source: local reproduction — `npm init @eslint/config@latest -- --config eslint-config-airbnb` run with closed stdin, and again with `CI=true` set, both crash with `npm error code 13`/unsettled top-level await and write no file. Separately, today's reproduction ran 8 fresh-directory trials each of `printf "n\n" | ...`, `printf "n\nn\n" | ...`, and `yes n | ...` / `yes y | ...` against @eslint/create-config@2.0.0 (current npm latest): `yes n | ...` succeeded 8/8 and `yes y | ...` succeeded 3/3, while both printf forms kept failing roughly half the time. CLI source read at `@eslint/create-config@2.0.0`'s `bin/create-config.js` and `lib/config-generator.js:392-400` — `generator.output()`, called on both the `--config` and no-`--config` paths, unconditionally calls `enquirer.prompt(installationQuestions)`.) This is the only config format ESLint understands as of **ESLint 10**, released February 2026 — the old `.eslintrc.*` system was removed completely, and **ESLint 9 went end-of-life on August 6, 2026**. If a repo still has `.eslintrc.json` or similar, it is silently ignored by a current ESLint install, not "still working in legacy mode." Migrate it.

   Humans at a terminal can drop `-- --config <package>` and run `npm init @eslint/config@latest` plain — that opens the interactive wizard, which is fine when a person is there to answer both its prompts. An unattended agent should supply `--config` and pipe an answer to the install-confirmation prompt that always follows; there is no flag that runs the whole command non-interactively.

4. **Prettier — no generator (this is normal)**
   Prettier does not ship an init command. Install it:
   ```
   npm i -D prettier
   ```
   and hand-write a small `.prettierrc` (JSON/YAML) or `prettier.config.js`. This is the one file in this list that's *supposed* to be hand-written — Prettier is intentionally low-configuration, and the project maintainers have repeatedly declined to add an init wizard.

5. **Biome — biome.json (alternative or supplement to ESLint+Prettier)**
   ```
   npm i -D -E @biomejs/biome
   npx @biomejs/biome init
   ```
   (`pnpm add -D -E @biomejs/biome` then `pnpx @biomejs/biome init` on pnpm). Generates `biome.json` with no interactive prompts. The `-E` flag pins the exact version — Biome's config format has moved between versions, so an unpinned install can drift.

   If replacing an existing ESLint/Prettier setup instead of starting fresh:
   ```
   npx @biomejs/biome migrate eslint --write
   npx @biomejs/biome migrate prettier --write
   ```
   These read your existing configs and translate them — don't hand-port ESLint rules into Biome's rule names from memory. Both commands are non-interactive already; `--write` applies the change instead of just previewing it.

   Decide once: ESLint+Prettier, or Biome. Running both linting stacks on the same files is redundant and they will disagree on formatting.

6. **Vitest — vitest.config.ts (hand-written, and that's correct)**
   ```
   npm i -D vitest
   ```
   Then write `vitest.config.ts` yourself using `defineConfig` from `'vitest/config'`. Vitest does not have a project-scaffold generator for its base config — the config file is meant to be authored directly, same as Prettier's.

   Browser-mode testing is the one exception, and it needs a human at a terminal: `npx vitest init browser` prompts you to choose a provider (Playwright, WebdriverIO, or preview) and there is no documented flag to pre-answer that choice. With stdin closed or non-TTY, the wizard does not hang — it exits cleanly (code 0) after only the first prompt, writing no config and installing nothing: a silent no-op, not a hang. An agent that only checks the exit code would wrongly read this as success. An agent setting up browser-mode testing should skip the generator and instead install the provider package directly (`@vitest/browser-playwright` or `@vitest/browser-webdriverio`) and hand-write the browser block in `vitest.config.ts`, the same authoring pattern already used for the base config. (Source: local reproduction — `npx vitest init browser < /dev/null` run in three separate fresh directories, plain, repeated, and with `CI=true`, all exit 0 in under a second with no files written, against vitest@4.1.11, current npm latest.)

7. **Playwright — playwright.config.ts + tests/**
   ```
   npm init playwright@latest -- --quiet --lang=<js|TypeScript> --browser=<chromium|firefox|webkit> --gha --install-deps
   ```
   (`pnpm create playwright` / `yarn create playwright` work the same way, and take the same flags.) `--quiet` is what suppresses the interactive prompts; the other flags supply the answers — language, browser, whether to add a GitHub Actions workflow — that the wizard would otherwise ask for. There is no `--test-dir` flag in the current CLI: the only way to control the target directory is the `rootDir` positional argument, not a flag — passing `--test-dir=<dir>` fails with `error: unknown option '--test-dir=mytests'`. Without `--quiet` and its answer flags, and with stdin closed, the wizard does not block indefinitely — it exits immediately (code 0) after only its first prompt and generates no files: a silent no-op, not a hang. An agent that only checks the exit code would wrongly read this as success, so always pass `--quiet` plus its answer flags for an unattended run. Generates `playwright.config.ts`, an example spec file, and the folder structure. Safe to re-run later without clobbering existing tests, though a human should still read what it's about to do before accepting — see Traps. (Source: local reproduction today — `npm exec --yes create-playwright@latest -- --help` lists only `--browser`, `--no-browsers`, `--no-examples`, `--install-deps`, `--next`, `--beta`, `--ct`, `--quiet`, `--gha`, `--lang`; `npm init playwright@latest -- --quiet --lang=js --browser=chromium --gha --test-dir=mytests --install-deps` fails with `error: unknown option '--test-dir=mytests'`.)

   Humans at a terminal can drop the flags and run `npm init playwright@latest` plain to walk through the wizard themselves.

## Choosing

**Package manager: pnpm vs npm.** Both `pnpm init` and `npm init -y` just write a bare `package.json` with no prompts, so either is safe for an agent to run. pnpm is faster and uses less disk because it hard-links packages instead of copying them into every project — worth it as the default for new work. Stick with npm only when the repo, CI pipeline, or team has already standardized on it; don't switch a working repo just to switch.

**ESLint + Prettier vs Biome.** Pick one, not both — running both linting stacks on the same files means they'll disagree about formatting. ESLint + Prettier is the safer default when you need ESLint's much larger rule/plugin ecosystem, or when agent tooling matters: ESLint ships an official MCP server (`@eslint/mcp`) that lets an agent lint, fix, and get rule explanations straight from the real engine. Biome has no equivalent — a request for an MCP server or llms.txt (biomejs/biome#8705) was closed "not planned" in January 2026 — so an agent working with Biome is on its own for first-party AI tooling, even though Biome itself (one fast Rust binary doing both lint and format) is simpler to run and configure.

**Playwright MCP vs Playwright CLI.** Microsoft ships both, and its own `playwright-cli` README draws the line: use the CLI when a coding agent is writing and running a checked-in test suite in a repo — it skips streaming full accessibility trees and screenshots into the model's context, which Playwright's own docs describe qualitatively as a lower token cost than MCP (source: playwright.dev/docs/getting-started-cli, playwright.dev/agent-cli/introduction). Playwright's own sources don't give a specific multiplier; a third-party estimate (testcollab.com) puts it at roughly 4x fewer tokens, but that number is not Playwright's own figure. Use the MCP server when an agent needs live, interactive browser control through a chat client, or for long-running autonomous exploration where holding continuous browser state matters more than token cost (self-healing tests, open-ended exploration).

**Vitest: hand-written config vs `vitest init browser`.** For a normal Vitest setup, always hand-write `vitest.config.ts` — there's no generator for the base config, and that's intentional. Reach for `vitest init browser` only when you're specifically standing up browser-mode testing; it installs the right provider package and prompts you to choose one (Playwright or WebdriverIO). Because that prompt has no documented bypass flag, with stdin closed the wizard exits cleanly (code 0) after its first prompt without ever reaching the provider question — a silent no-op that reads as success to anything checking only the exit code, not a hang. An agent running unattended should skip the generator and install the provider package plus hand-write the browser config block itself, same as it would for the base config.

**TypeScript module defaults: bundler vs nodenext.** `tsc --init` now defaults `module` to `nodenext` and writes no explicit `moduleResolution` key at all — it resolves as NodeNext by implication. That default is already correct for a library or anything that runs directly under Node.js (no bundler in front of it); it needs no override. For an app built by Vite, esbuild, or Webpack, override after generating the file — to something like `module: "preserve"` with `moduleResolution: "bundler"` — since the bundler, not `tsc`, controls resolution; don't leave the Node-oriented default in place just because it's what the wizard picked. (Source: direct execution of `npx tsc --init` against fresh installs of typescript@7.0.2 and typescript@6.0.3 from registry.npmjs.org — reproducible by running the command today. This is stronger ground truth than the TSConfig reference page, whose generic per-option "Default" column describes compiler behavior when a flag is entirely absent, not what `--init` actually writes into the file.)

**TypeScript 7.0 vs 6.x: which one to install.** TypeScript 7.0 (Go-native compiler) is GA as of July 8, 2026, and is what `npm i -D typescript` installs by default (source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/). Install 7.0 for a plain TypeScript project. If the project depends on a Volar-based framework toolchain (Vue, Astro, Svelte, MDX), pin to TypeScript 6.x instead — those tools need TS's programmatic API, which 7.0 doesn't have yet (expected in 7.1). If the project depends on typescript-eslint specifically, don't pin down to 6.x — alias two devDependencies instead: `"typescript": "npm:@typescript/typescript6@^6.0.2"` to satisfy typescript-eslint's peer dependency, and `"@typescript/native": "npm:typescript@^7.0.2"` so `tsc` still gets the fast 7.0 compiler (source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/).

## Never hand-write

These files have an official generator. Writing them from memory means guessing at a schema that changes between versions — use the command, then edit the result.

- `tsconfig.json` — from `tsc --init`
- `eslint.config.js` / `eslint.config.mjs` — from `npm init @eslint/config@latest` (with `--config <package>` plus a continuous piped stdin stream — `yes n |` / `yes y |` — answering the install-confirmation prompt, for an unattended agent — see Traps)
- `biome.json` / `biome.jsonc` — from `biome init` (or `biome migrate ...`)
- `playwright.config.ts` and its generated `tests/` scaffold — from `npm init playwright@latest`
- `package.json`'s initial skeleton — from `npm init` / `pnpm init`

**Not on this list, on purpose:** `.prettierrc`/`prettier.config.js` and `vitest.config.ts`. Neither tool ships a generator for its base config — hand-authoring those two is the documented, correct workflow, not a shortcut.

Important nuance: "never hand-write" means don't *originate* the file from memory. Editing a generated file afterward — adding a compiler flag, adding an ESLint rule override, adding a Playwright project — is normal and expected. The rule blocks skipping the generator, not blocking all edits forever.

## Thorough setup checklist

A properly set-up js-ts-core repo, before any feature work starts, has:

- `package.json` created by `npm init`/`pnpm init`, with a package manager choice that was made on purpose, not copied from the last repo
- `tsconfig.json` from `tsc --init`, edited for the project's actual target/module needs, on current TypeScript defaults (strict, ESM, modern target) unless there's a stated reason to loosen it — on TypeScript 7.0 by default, pinned to 6.x if the project needs a Volar-based framework toolchain, or on 7.0 with both `typescript` aliased to `@typescript/typescript6` (to satisfy typescript-eslint's peer dependency) and `@typescript/native` aliased to the real `typescript@7` package (to keep `tsc` on 7.0) if the project needs typescript-eslint
- ESLint on v10 with `eslint.config.js`/`.mjs` from the config wizard (or `--config <package>` plus a continuous piped stdin stream — `yes n |` / `yes y |` — answering the install-confirmation prompt for a non-interactive run), and no leftover `.eslintrc.*` files anywhere in the repo
- Either Prettier (`.prettierrc`, hand-authored) or Biome (`biome.json`, from `biome init`) for formatting — not both fighting each other
- Vitest installed with a hand-written `vitest.config.ts` using `defineConfig`
- Playwright installed via `npm init playwright@latest` (with `--quiet` and its answer flags for an unattended run), with `playwright.config.ts` and a working example test that actually runs
- `package.json` scripts wired up: something like `lint`, `format`, `test`, `test:e2e`, `typecheck`, each calling the tool that was actually installed
- `.gitignore` covering `node_modules`, build output, and coverage — check what the scaffolds already added before adding your own

## Traps

**`npm init @eslint/config@latest` crashes or hangs an unattended agent, even with `--config`.** `--config <shareable-config-package>` only skips the first prompt (which shareable config to use). A second, unconditional prompt — "Would you like to install them now?" — always fires afterward, with no flag to bypass it. With no TTY and closed stdin, that second prompt crashes the process rather than hanging cleanly. A single piped line is NOT a reliable fix: `printf "n\n" | npm init @eslint/config@latest -- --config <pkg>` races against when the underlying enquirer prompt's keypress listener attaches, so the answer can be consumed before the prompt is ready — in 8 fresh-directory trials this crashed with `npm error code 1` / '✖ Operation canceled' (no file written) 5 times out of 8, and adding a second piped line didn't fix it (still 3/8 failures). Use a continuous stream instead: `yes n | npm init @eslint/config@latest -- --config <pkg>` (or `yes y | ...` to auto-install) — reliable 8/8 in testing because it keeps stdin available no matter when the listener attaches. (Source: local reproduction today — 8 trials each of `printf "n\n" | ...`, `printf "n\nn\n" | ...`, and `yes n | ...`, against @eslint/create-config@2.0.0. CLI source read at `@eslint/create-config@2.0.0`'s `bin/create-config.js` and `lib/config-generator.js:392-400` — `generator.output()`, called on both the `--config` and no-`--config` paths, unconditionally calls `enquirer.prompt(installationQuestions)`.)

**Hand-stitching `eslint.config.js` from a remembered ESLint 8/9 example.** ESLint 10 removed eslintrc entirely and the flat-config API has settled into a shape that differs from older blog-post examples. Run the wizard (with `--config` and a piped stdin answer to the install-confirmation prompt for an unattended agent). (Source: eslint.org/blog/2026/02/eslint-v10.0.0-released/.)

**`.eslintrc.*` doesn't error on ESLint 10 — it's just silently ignored.** A repo that still has a legacy `.eslintrc.json` lying around will pass a current ESLint install with what looks like a working config, but none of those rules are actually being applied. It's the single easiest way to think a project is linted when it isn't. (Source: eslint.org/blog/2026/02/eslint-v10.0.0-released/ — the flat-config rollout removed eslintrc support entirely.)

**ESLint v9 stopped getting patches on August 6, 2026.** A repo pinned to v9 keeps running today, but it gets no more bug or security fixes going forward — treat any v9 pin found during setup as debt to flag, not a stable choice. (Source: eslint.org/version-support/.)

**Typing out a "standard" `tsconfig.json`** with pre-6.0 defaults (loose module resolution, no strict mode) because that's what training data has memorized. `tsc --init` on a current TypeScript install already reflects the strict, ESM, modern-target defaults introduced in 6.0 and still present in 7.0; starting from the generator avoids silently downgrading the project's type safety. (Source: devblogs.microsoft.com/typescript/announcing-typescript-7-0/.)

**Treating Prettier's lack of an init command as a gap to fill in** by writing an elaborate `.prettierrc` with every option specified. Prettier is deliberately opinionated with few options — a short config is normal, not incomplete. (Source: prettier.io/docs/install.)

**`npx vitest init browser` does not hang an unattended agent — it silently no-ops instead.** With stdin closed (tested plain and with `CI=true`), the wizard prints only its first prompt ("Choose a language for your tests") and exits immediately with code 0 — it never reaches the provider-choice prompt (playwright/webdriverio/preview), writes no `vitest.config.ts`, and installs nothing. That is arguably worse than a hang for an unattended agent: the exit code reads as success while nothing happened. Skip the generator regardless, and hand-write the browser config block plus install the provider package directly. (Source: local reproduction today — `npx vitest init browser < /dev/null`, run three times in fresh directories against vitest@4.1.11 (current npm latest), twice plain and once with `CI=true`; all exit 0 in under a second with no files written. A separate run piping several Enter keystrokes confirms the real second prompt is "Choose a browser provider" with playwright/webdriverio/preview options, matching the page's description of the prompt content — only the hang claim is wrong.)

**Assuming Vitest needs a "vitest init" command because every other tool in this list has one.** It doesn't, outside browser mode; hand-authoring `vitest.config.ts` is correct, not a shortcut being taken. (Source: vitest.dev/guide/browser/, vitest.dev config docs.)

**`npm init playwright@latest` with no flags does not block/hang on closed stdin — it silently no-ops.** The `--help` flag list is accurate as documented (`--quiet`, `--lang`, `--browser`, `--gha`, `--install-deps`, `--no-browsers`, `--no-examples`, `--next`, `--beta`, `--ct`; no `--test-dir` — the target directory is the positional `rootDir` argument, confirmed by `--test-dir=mytests` failing with `error: unknown option '--test-dir=mytests'`). But run plain with stdin closed, the command does not block: it prints only the first prompt ("Do you want to use TypeScript or JavaScript?") and exits with code 0, creating no `playwright.config.ts` and no `tests/` folder. Still always pass `--quiet` and its answer flags for an unattended run — the real failure mode to guard against is a silent no-op that reads as success, not an indefinite hang. (Source: local reproduction today — `npm init playwright@latest < /dev/null`, run to completion in the foreground: exit code 0, only the language prompt printed, no files created.)

**Re-running `npm init playwright@latest` and accepting prompts blindly**, overwriting an existing tests folder's name choice. The command is safe to re-run but the prompts still need a human/agent to read them, not auto-accept. (Source: github.com/microsoft/create-playwright/blob/main/src/cli.ts.)

## AI and agent resources

Checked as of August 2026: TypeScript, ESLint, Biome, Vitest, and Playwright.

Three of the five tools in this stack ship something official for AI agents. Two do not.

- **Playwright — official MCP server.** Package `@playwright/mcp`, documented at playwright.dev/docs/getting-started-mcp. Run `npx @playwright/mcp@latest` and add it to your MCP client config. It gives an agent real browser control — navigate, click, type, screenshot, inspect network calls — using structured accessibility snapshots instead of raw pixels. One caveat from Playwright's own 2026 docs: for coding-agent workflows (writing and running test suites in a repo), they now recommend the Playwright CLI over MCP because it costs fewer tokens per session — Playwright's own docs (playwright.dev/docs/getting-started-cli, playwright.dev/agent-cli/introduction) state this qualitatively as lower vs. higher token cost, without a specific multiplier. A third-party estimate (testcollab.com) puts the difference at roughly 4x fewer tokens, but that figure is not Playwright's own. Use the MCP server when an agent needs live, interactive browser control through a chat client; use the CLI when it's driving a checked-in test suite.

- **ESLint — official MCP server.** Package `@eslint/mcp`, documented at eslint.org/docs/latest/use/mcp. Run `npx @eslint/mcp@latest`, register it in the editor's MCP config, and an agent can lint a file, auto-fix violations, and get an explanation of why a rule fired — straight from ESLint's real engine, not guessed from memory.

- **Vitest — official llms.txt and llms-full.txt.** Live at vitest.dev/llms.txt (a navigable index) and vitest.dev/llms-full.txt (the full docs bundle, tens of thousands of words, including the complete assertion and matcher API). Fetch the index first to find the right section, or pull the full bundle into context before nontrivial test-writing or config work.

- **TypeScript — nothing official.** No llms.txt, no MCP server, no AI-facing docs page on typescriptlang.org as of this check. If you need current TypeScript API details for an agent, use general-purpose docs lookup (e.g. Context7) rather than assuming a first-party feed exists.

- **Biome — nothing official, and it's a deliberate no for now.** Biome's repo has an AGENTS.md, but it's internal contributor guidance for agents working on Biome's own codebase, not a resource for agents using Biome in a project. A GitHub issue asking Biome to ship an MCP server or llms.txt (biomejs/biome#8705) was closed as "not planned" in January 2026.

Bottom line: reach for the Playwright and ESLint MCP servers when an agent is running browser tests or linting, and Vitest's llms.txt when it's writing or configuring tests. For TypeScript and Biome, there's no first-party agent feed to lean on.
