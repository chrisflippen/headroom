<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: create-solid | npm view create-solid version | 0.12.0 -->
<!-- probe: @solidjs/start | npm view @solidjs/start version | 2.0.4 -->
<!-- probe: create-qwik | npm view create-qwik version | 1.20.0 -->
# solid-qwik scaffold-first reference (researched 2026-08-30)

Covers the performance-first engines: **SolidStart** (Solid's full-stack framework, fine-grained reactivity, no virtual DOM) and **Qwik City** (resumability — no hydration cost at all). Both scaffolded, booted, and SSR-verified live this session (macOS arm64, Node v26.7.0); SolidStart's vitest template test passed, Qwik's build (which includes its typecheck) passed.

## Official setup commands (all run live)

| Framework | Command | Notes |
|---|---|---|
| SolidStart | `npx create-solid@latest <dir> with-vitest -s --v2 --ts` | The creator prompts UNLESS every axis is pinned: `-s` (SolidStart) + `--v2` (version) + `--ts` + a template as the second positional. `with-vitest` ships a working test; other templates observed live: `with-tailwindcss`, `with-trpc`, `with-auth`, `with-solidbase`, `with-strict-csp`, `with-unocss`. Does NOT install deps — `npm install` after. |
| Qwik | `npx create-qwik@latest empty ./<dir> -i` | Template is the first positional. Valid choices at this check: `empty`, `playground`, `library`, `e2e-library`. `-i` installs deps. Add integrations/adapters afterward with `npm run qwik add`. |

Boot checks that worked here: SolidStart `npm run dev` → curl on :3000 returned server-rendered HTML with Solid's hydration script. Qwik `npm run dev` → curl on :5173 returned SSR HTML carrying the resumability markers (`q:container="paused"`). SolidStart `npm test` (Vitest, from the template) passed untouched; Qwik `npm run build` (runs `tsc --noEmit` + client and SSR builds) passed untouched.

## Stale-training corrections (verified live)

- **Solid 2.0 exists (RC) and the creator's default flow asks about it.** `-s --v2` pins today's stable SolidStart 2 on Solid 1.x; `--solid` scaffolds the 2.0 RC line. Re-check this page's probes before assuming which is stable.
- **Qwik's `base` template is GONE.** Docs and memory that say `npm create qwik@latest base ./app` are stale — the CLI rejects it and lists `empty`/`playground`/`library`/`e2e-library`. `empty` is the app starter now.
- **A closed-stdin run of `create-solid` exits 0 having created NOTHING** — see Traps.

## Never hand-write

- SolidStart's `vite.config.ts` with the `solidstart` plugin wiring, and Qwik's `vite.config.ts` + `qwik.env.d.ts` — generated; extend, don't recreate.
- Qwik adapter/integration wiring — added via `npm run qwik add <integration>`, never hand-assembled.
- Lockfiles, as always.

## Thorough setup checklist

1. Pick the lane: SolidStart for a Solid/JSX app with SSR and actions; Qwik when startup performance on low-end devices is the actual requirement (resumability is its whole point).
2. Scaffold with the exact pinned flags above; SolidStart then needs `npm install`.
3. Boot check: dev server + curl; for Qwik confirm the `q:container` markers are in the HTML — that's resumability working.
4. Tests: SolidStart — use `with-vitest` so a working test ships; other templates need Vitest added per `js-ts-core.md`. Qwik's `empty` ships lint + typecheck but no test runner — add Vitest the same way.
5. Qwik deploy targets go through `npm run qwik add` (its `deploy` script literally tells you this) — pick the adapter before writing deploy CI.
6. `git init` yourself — neither creator made a repo here.

## Traps

**`create-solid` under closed stdin exits 0 with nothing created.** Every unpinned axis becomes an interactive prompt, and in a non-TTY run the prompt dies silently with a success exit code. Worse, a `yes ''` stream (the electron-vite trick) does NOT work — the arrow-key menu just hangs. The only reliable unattended path is pinning everything: name, template positional, `-s`/`--solid`, `--v2`, `--ts`. Check the directory actually exists before proceeding. (Observed 2026-08-30.)

**The SolidStart template ships a `pnpm-lock.yaml` even though you'll likely use npm.** After `npm install` you have BOTH lockfiles; delete the one for the package manager you're not using, or CI will resolve against the stale one. (Observed 2026-08-30.)

**Qwik's dev server takes :5173, SolidStart's takes :3000** — the usual port collisions with other boot checks. (Observed 2026-08-30.)

## AI and agent resources

Checked by direct HTTP status (curl -L), 2026-08-30:

- `https://docs.solidjs.com/llms.txt` → 200 — fetch before nontrivial Solid work.
- `https://qwik.dev/llms.txt` → 404 at this check — use Context7 for Qwik docs.
- Both creators' `--help` output is accurate and is where the flags above came from — run it before trusting this table if the probes show drift.
