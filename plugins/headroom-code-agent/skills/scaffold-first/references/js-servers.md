<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: create-hono | npm view create-hono version | 0.19.5 -->
<!-- probe: hono | npm view hono version | 4.13.5 -->
<!-- probe: fastify-cli | npm view fastify-cli version | 8.0.0 -->
<!-- probe: @nestjs/cli | npm view @nestjs/cli version | 12.0.0 -->
# js-servers scaffold-first reference (researched 2026-08-30)

Covers the three JS/TS server frameworks: **Hono** (tiny, web-standard, runs anywhere), **Fastify** (fast Node-native API server, plugin architecture), **NestJS** (batteries-included application framework, Angular-style DI). All three scaffolded, booted, and hit live on this machine (macOS arm64, Node v26.7.0, npm 11.19.0) this session; Fastify and Nest's own scaffold tests executed and passing.

When to reach for which: Hono for small/edge-portable APIs and when the deploy target is a serverless runtime (its templates map 1:1 to targets); Fastify for a plain fast Node API with a mature plugin ecosystem; Nest when the project wants a full framework (modules, DI, guards, its own CLI generators for every artifact). FastAPI/Django (`python-web.md`) remain the Python lane; this page is the same-language-as-the-frontend lane.

## Official setup commands (all run live)

| Framework | Command | Notes |
|---|---|---|
| Hono | `npx create-hono@latest <dir> -t nodejs -p npm -i` | With `-t` and `-p` given it asks nothing (verified, stdin closed). 13 templates, one per runtime/deploy target: `nodejs`, `bun`, `deno`, `cloudflare-workers`, `aws-lambda`, `vercel`, `netlify`, ... Pick by where it will run. `-i` installs deps. |
| Fastify | `npx fastify-cli@latest generate <dir> --lang=ts` | Generates the plugin-structured project (`src/app.ts`, `src/plugins/`, `src/routes/`) with tests. Does NOT install deps — run `npm install` after (verified: generate exits telling you to). `--esm` exists for the JS template. |
| NestJS | `npx @nestjs/cli@latest new <dir> --package-manager npm --skip-git` | With `--package-manager` given it asks nothing (verified, stdin closed). Full app + unit and e2e tests. Add artifacts later with `npx nest generate resource\|controller\|service <name>`. |

Boot checks that worked here: Hono `npm run dev` → `Server is running on http://localhost:3000` → curl returned `Hello Hono!`. Fastify `npm start` → pino log `Server listening at http://[::1]:3000` → curl returned `{"root":true}`. Nest `npm run start` → curl returned `Hello World!`. All three default to port 3000 — only one at a time.

Tests: Fastify `npm test` (Node's built-in test runner + c8 coverage — not jest/vitest) passed at 100% coverage untouched. Nest `npm test` and `npm run test:e2e` (Vitest, see below) both passed untouched. Hono's `nodejs` template ships NO test script (see Traps).

## Stale-training corrections (verified in the generated output)

- **Nest 12 generates Vitest, not Jest** — `vitest.config.ts` + `vitest.config.e2e.ts`, scripts `vitest run`. Memory says Nest = Jest; the scaffold says otherwise now.
- **Nest 12 generates oxlint (`oxlint.json`), not ESLint + Prettier.** Don't bolt ESLint onto a fresh Nest app from memory.
- **Fastify's generated tests use `node --test` + c8**, not tap (its historic runner) and not jest.

## Never hand-write

- `nest-cli.json` — `nest new` generates it; `nest generate` reads it. Edit values, never create from scratch.
- Fastify's `src/app.ts` autoload wiring and the `src/plugins`/`src/routes` layout — generated; add routes as new files in `src/routes/`, they autoload.
- Nest's `vitest.config*.ts`, `oxlint.json`, `tsconfig.build.json` — scaffold-generated; tune, don't retype.
- Lockfiles, as always.

## Thorough setup checklist

1. Pick the framework by the table above; for Hono pick the template by deploy target — moving targets later means re-scaffolding to compare wiring.
2. Scaffold with the exact non-interactive flags shown, then `npm install` for Fastify.
3. Boot check: start it, curl `localhost:3000`, see the framework's hello response.
4. Tests: run the scaffold's own suite (Fastify, Nest) before touching anything. For Hono, add Vitest per `js-ts-core.md` — the template gives you none.
5. Nest: subsequent artifacts go through `nest generate`, not hand-copied boilerplate.
6. `git init` yourself for Hono and Fastify (neither creates a repo; Nest only with `--skip-git` omitted). Wire test + lint into CI per `ci-github-actions.md`.

## Traps

**Hono's `nodejs` template ships no tests and no lint** — just dev/build/start. It looks done; it isn't. Add Vitest and a linter per `js-ts-core.md` before feature work. (Observed 2026-08-30.)

**Hono generates a `pnpm-workspace.yaml` even when you chose npm.** It exists only to declare esbuild's build-script permission for pnpm users (`onlyBuiltDependencies`/`allowBuilds`). Harmless under npm; don't "clean it up" reflexively if pnpm might ever touch the repo, and don't mistake the project for a workspace. (Observed 2026-08-30.)

**`fastify-cli generate` exits successfully without installing anything.** The scaffold isn't runnable until `npm install`. A boot check straight after generate fails with missing modules — that's the missing install, not a broken scaffold. (Observed 2026-08-30.)

**All three bind port 3000 by default.** Sequential boot checks need the previous server killed first, or the new one errors/collides. (Observed 2026-08-30.)

## AI and agent resources

Checked by direct HTTP status (curl -L), 2026-08-30:

- `https://hono.dev/llms.txt` → 200
- `https://fastify.dev/llms.txt` → 200
- `https://docs.nestjs.com/llms.txt` → 200 (nestjs.com root also serves one)

Fetch the relevant one before nontrivial work in that framework; resolve API specifics through Context7 rather than memory.
