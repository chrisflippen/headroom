<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: @tanstack/cli | npm view @tanstack/cli version | 0.71.0 -->
<!-- probe: @tanstack/react-start | npm view @tanstack/react-start version | 1.168.49 -->
<!-- probe: create-react-router | npm view create-react-router version | 8.3.1 -->
# react-fullstack scaffold-first reference (researched 2026-08-30)

Covers the serious React full-stack lanes beyond Next.js: **TanStack Start** (type-safe file routing + SSR on Vite) and **React Router framework mode** (v8, the Remix successor). Both scaffolded, booted, and SSR-verified live this session (macOS arm64, Node v26.7.0). Next.js has its own page (`nextjs.md`); the house default for new web apps remains SvelteKit (`sveltekit.md`) — these are the React-ecosystem picks.

## Official setup commands (all run live)

| Framework | Command | Notes |
|---|---|---|
| TanStack Start | `npx @tanstack/cli@latest create <dir> --framework React --package-manager npm --toolchain biome` | Non-interactive with these flags (verified, stdin closed). `--toolchain eslint` also exists; `--blank` gives a minimal one-route app; `--deployment cloudflare\|netlify\|nitro\|railway` picks the adapter — railway matches our hosting today. |
| React Router | `npx create-react-router@latest <dir> --yes` | `--yes` accepts defaults non-interactively (verified). `--no-git-init` available. Generates a Dockerfile out of the box. |

Boot checks that worked here: TanStack `npm run dev` → Vite ready on :3000 → curl returned server-rendered HTML (root route markup). React Router `npm run dev` → curl on :5173 returned server-rendered HTML (welcome page). Verification the scaffolds ship: TanStack has `npm run check` (Biome; see Traps) and no test script; React Router has `npm run typecheck` (`react-router typegen && tsc`) — passed untouched.

## Stale-training corrections (verified live)

- **`@tanstack/create-start` is DEPRECATED.** Running it prints the deprecation and points at the replacement: `npx @tanstack/cli create` (or `tanstack create`). Memory that says create-start (or create-tsrouter-app for the router) is behind — the unified CLI is the official path now.
- **"Remix" is React Router now.** The framework-mode scaffold, docs, and the v8 packages all live under react-router; `create-react-router` is the creator. Don't scaffold with `create-remix` for new work.
- **TanStack Start scaffolds Tailwind always** — the `--tailwind` flag is deprecated-as-default; `--blank` is the only way to skip it.

## Never hand-write

- `tsr.config.json` and the generated route tree (`src/routeTree.gen.ts`) — TanStack's CLI/`tsr generate` own them.
- `.react-router/` type-gen output and `react-router.config.ts`'s generated shape — regenerate via `react-router typegen`; edit the config's values, don't retype the file.
- Both scaffolds' `vite.config.ts` skeletons — extend, don't recreate.
- `AGENTS.md` from the TanStack scaffold — agent-facing instructions the CLI maintains; keep it, per the SKILL.md rule.

## Thorough setup checklist

1. Pick the lane: TanStack Start when end-to-end type-safety of routes/search-params is the priority; React Router when the team knows Remix idioms or wants the Dockerfile-ready server output.
2. Scaffold with the exact flags above; TanStack: choose `--toolchain` and (if deploying) `--deployment` up front.
3. Boot check: dev server + curl, confirm actual server-rendered HTML, not a blank shell.
4. TanStack ships NO tests — add Vitest per `js-ts-core.md`. React Router: `npm run typecheck` is the floor; add Vitest the same way.
5. Run the scaffold's own check (`npm run check` / `npm run typecheck`) before feature work — and see Traps for TanStack's.
6. `git init` yourself for TanStack (React Router asks/creates unless `--no-git-init`).

## Traps

**TanStack's own `npm run check` FAILS on the untouched scaffold, and `npm run format` does NOT fix it** — the script is `biome format` with no `--write`, so it only prints. Three separate problems, all shipped by the generator (verified live): unformatted generated files, a `biome.json` pinned to an older schema than the installed CLI, and the scaffold's own `__root.tsx` theme-init script tripping Biome's `noDangerouslySetInnerHtml` security rule. The full green path, run and confirmed exit 0: `npx biome check --write .` then `npx biome migrate --write` then a `biome-ignore lint/security/noDangerouslySetInnerHtml` comment on the scaffold's own `<script dangerouslySetInnerHTML...>` line in `src/routes/__root.tsx`. Commit that once; then the check is meaningful. Don't burn time thinking you broke it. (Observed 2026-08-30, @tanstack/cli 0.70.2, Biome 2.4.5.)

**The deprecated creator still "works",** so nothing forces you onto the new CLI — you find out from a yellow warning line easy to miss in scrollback. Use `@tanstack/cli`; the old name will rot. (Observed 2026-08-30.)

**Both dev servers claim common ports** (3000 TanStack — same as every JS server framework; 5173 React Router/Vite). Kill previous boot checks first. (Observed 2026-08-30.)

## AI and agent resources

Checked by direct HTTP status (curl -L), 2026-08-30:

- `https://tanstack.com/llms.txt` → 200; the TanStack scaffold also generates a project-local `AGENTS.md` — honor it.
- `https://reactrouter.com/llms.txt` → 404 at this check — use Context7 for React Router docs.
