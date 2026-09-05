<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: create-vite | npm view create-vite version | 9.2.0 -->
<!-- probe: preact | npm view preact version | 10.29.8 -->
<!-- probe: lit | npm view lit version | 3.3.3 -->
<!-- probe: create-preact | npm view create-preact version | 0.5.3 -->
# preact-lit scaffold-first reference (researched 2026-08-30)

Covers the tiny-footprint niches: **Preact** (3kB React-compatible) and **Lit** (web components on browser standards). Both verified live this session (macOS arm64, Node v26.7.0): Preact scaffolded, served, and production-built; Lit's official starter built, served, and its 5 tests passing in three real browsers — after two out-of-the-box failures documented in Traps.

## Official setup commands (all run live)

| Framework | Command | Notes |
|---|---|---|
| Preact | `npm create -y vite@latest <dir> -- --template preact-ts` | Vite's creator with the first-party Preact template — truly non-interactive (verified). `preact` (JS) template also exists. Then `npm install`. **Do not use `npm init preact` unattended** — see Traps. |
| Lit | `git clone --depth 1 https://github.com/lit/lit-element-starter-ts.git <dir>` | Lit's official starter kits are GitHub template repos (confirmed live on lit.dev's starter-kits page): `lit-element-starter-ts` / `-js`. No npm creator exists (`@lit/create` is not a package). Then `npm install` and apply the Traps fixes below before anything else. |

Boot checks that worked here: Preact `npm run dev` → curl on :5173 returned the app HTML; `npm run build` (tsc + vite) passed. Lit `npm run serve` → curl on :8000/dev/index.html returned the component demo page; `npm test` → 5 passed in Chromium, Firefox, AND Webkit (web-test-runner + playwright launchers).

## Never hand-write

- Vite template output (`vite.config.ts`, the `tsconfig.*.json` triple) — extend, don't recreate.
- The Lit starter's `web-test-runner.config.js` and `rollup.config.js` — starter-owned; tune values in place.
- A web component's `customElements.define` boilerplate — copy the starter's `my-element.ts` shape, don't reinvent registration from memory.

## Thorough setup checklist

1. Pick the lane: Preact when you want the React model at minimal size; Lit when the deliverable is framework-agnostic web components (design systems, embeds).
2. Preact: scaffold via create-vite as above; the template has no tests — add Vitest per `js-ts-core.md`.
3. Lit: clone the starter, `npm install`, then IMMEDIATELY apply the two Traps fixes (tsconfig + test-runner bump) and `npx playwright install` — the starter does not work as cloned on current Node.
4. Lit is a cloned repo: `\rm -rf .git` and `git init` fresh so you're not carrying the starter's history; rename the package and element from `my-element` before building on it.
5. Run the full check before feature work: Preact `npm run build`; Lit `npm run build && npm test`.

## Traps

**`npm init preact` / `create-preact` cannot run unattended AT ALL.** Under closed stdin it crashes (`ERR_TTY_INIT_FAILED` from its prompt library); under a pseudo-terminal (expect, `script`, piped newline streams — all tried) it prints its banner and dies without creating anything, exit 0. It has no flags beyond a directory positional. For agent work use the create-vite path above, which Preact's own docs also list. (Observed 2026-08-30, create-preact 0.5.3.)

**The Lit starter does not compile as cloned on a current toolchain.** `npm run build` fails with TS7016 in `@open-wc` types — the starter pins `"module": "es2020"` / `"moduleResolution": "node"`, and current @open-wc packages need modern resolution. Fix that worked: set `"module": "es2022"` and `"moduleResolution": "bundler"` in tsconfig.json. (Observed 2026-08-30.)

**The Lit starter's tests crash on Node 26 before running anything.** Its pinned `@web/test-runner` pulls an old `@puppeteer/browsers` whose extensionless CJS shim breaks under Node 26 ES-module semantics (`require is not defined in ES module scope`). Fix that worked: `npm i -D @web/test-runner@latest @web/test-runner-playwright@latest`, then `npx playwright install` for the browsers. After that, all 5 starter tests pass in all three engines. (Observed 2026-08-30.)

**npm 11's install-scripts gating fires on the starter's `core-js-bundle` postinstall** — same gating behavior as documented in `electron.md`; approve or ignore consciously (`npm install-scripts ls`). (Observed 2026-08-30.)

## AI and agent resources

- `https://preactjs.com/llms.txt` → 200 (checked 2026-08-30) — fetch before nontrivial Preact work. `https://lit.dev/llms.txt` → 404 — use Context7 for Lit.
- lit.dev's starter-kits docs page is the authority on the starter repos (scraped live for this page).
