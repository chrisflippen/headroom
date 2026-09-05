<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: electron | npm view electron version | 44.2.0 -->
<!-- probe: create-electron-app | npm view create-electron-app version | 7.11.2 -->
<!-- probe: @quick-start/create-electron | npm view @quick-start/create-electron version | 1.0.30 -->
# electron scaffold-first reference (researched 2026-08-30)

Covers desktop apps with **Electron**: the two real scaffolders — **Electron Forge** (`create-electron-app`, the Electron org's own all-in-one tool; electronjs.org's quick-start hands packaging to it) and **electron-vite** (`npm create @quick-start/electron`, the Vite-based alternative with framework templates including Svelte) — plus the verified Playwright boot check that proves a scaffold actually launches. Every command below was run live on this machine (macOS arm64, Node v26.7.0, npm 11.19.0) with exit codes checked directly, and both scaffolds were booted to a real rendered window (screenshots taken and inspected, renderer→main IPC clicked and observed). Tauri — the Rust-backed desktop alternative — has its own verified page: `references/tauri.md`.

## Official setup commands

| Step | Command | What it generates |
|---|---|---|
| Scaffold a Forge app | `npx create-electron-app@latest <dir> --template=vite-typescript` | `forge.config.ts`, `vite.main.config.ts`, `vite.preload.config.ts`, `vite.renderer.config.ts`, `tsconfig.json`, `.eslintrc.json`, `src/` (main.ts, preload.ts, renderer.ts), `index.html`, `.gitignore`, a git repo — and it runs `npm install` itself |
| Forge help / flags | `npx create-electron-app@latest --help` | prints usage: `-t/--template`, `-c/--copy-ci-files`, `-f/--force`, `--skip-git`, `--electron-version <version\|latest\|beta\|nightly>` |
| Scaffold an electron-vite app | `yes '' \| npm create @quick-start/electron@latest <dir> -- --template svelte-ts` | `electron.vite.config.ts`, `electron-builder.yml`, `eslint.config.mjs` (flat), `.prettierrc.yaml`, `svelte.config.mjs`, `tsconfig.json` + `tsconfig.node.json` + `tsconfig.web.json`, `src/{main,preload,renderer}`, `.gitignore` — does NOT run `npm install` (see Traps for why `yes '' \|` is required) |
| Install + un-gate binaries (electron-vite) | `npm install`, then `npm install-scripts approve electron esbuild` | npm 11 gates install scripts by default — see Traps |
| Modernize the electron pin (electron-vite) | `npm install --save-dev --save-exact electron@latest` | the scaffold pins an old Electron major (`^39.2.6` observed vs `44.0.0` latest); bumping is both the versions-always-latest rule and the fix for the Node-26 extractor trap |
| Build (electron-vite) | `npm run build` | runs typecheck (tsc + svelte-check) then `electron-vite build` → `out/main`, `out/preload`, `out/renderer` |
| Package for distribution | Forge: `npm run make` / `npm run package` (but see the Node 26 trap — Forge 7 cannot). electron-vite: `npm run build:mac` etc. (electron-builder, wired by the scaffold) | platform installers / `.app` bundles |

Verified versions at time of writing (`npm view <pkg> version`): `create-electron-app@7.11.2`, `electron@44.0.0`, `@quick-start/create-electron@1.0.30`, `electron-vite@5.0.0`, `@electron/packager@20.3.0` (but Forge 7 pins `@electron/packager@18.x` internally — this matters, see Traps). Forge dist-tags at this check: `{ latest: 7.11.2, alpha: 8.0.0-alpha.10 }`. Resolve all of these fresh at scaffold time.

**Forge templates** (from its docs, scaffold verified live with `vite-typescript`): `webpack`, `webpack-typescript`, `vite`, `vite-typescript`. The Forge docs mark Vite support "experimental", but it scaffolded, built, and booted cleanly here.

**electron-vite templates** (from its docs; `svelte-ts` verified live): `vanilla`, `vanilla-ts`, `vue`, `vue-ts`, `react`, `react-ts`, `svelte`, `svelte-ts`, `solid`, `solid-ts`.

## Choosing

**electron-vite + electron-builder is the default pick for house projects; Forge is the official-pipeline alternative.** The observed trade, from generating both on the same day:

- **electron-vite (`svelte-ts`)** generated modern tooling across the board: ESLint 9 flat config, Prettier 3, TypeScript 5.9, Vite 7, Svelte 5, `svelte-check`, and a complete `electron-builder.yml` for mac/win/linux distribution. Its one stale spot is the Electron pin itself (`^39.2.6`, four majors behind) — bump to `electron@latest` immediately after scaffolding (command above). It has framework templates, which Forge does not.
- **Forge (`vite-typescript`)** is the Electron org's own tool and owns the whole pipeline (dev, package, make installers, publish) in one config. But its template shipped `typescript@~4.5.4`, `eslint@8` with a legacy `.eslintrc.json` (the format ESLint 10 dropped), and Vite 5 — you inherit a modernization job on day one. And on this machine's Node 26, **Forge 7 stable cannot package at all** (silent failure, see Traps); packaging needed the Forge 8 alpha.

Both scaffolds pin `electron` exact/caret in devDependencies; both boot verifiably (screenshots inspected). If the project needs installers today with zero extra wiring and you accept the alpha channel, Forge still works; otherwise electron-vite's generated `electron-builder.yml` covers distribution with current-generation tooling.

## The boot check — prove the scaffold launches before feature work

`playwright-core` (NOT `playwright` — `_electron` lives in `playwright-core` and skips the browser-binary download) launches the real Electron binary, waits for the window, and screenshots it. Verified live against both scaffolds:

```js
// boot-check.mjs — run after `npm run build` (electron-vite) or after the
// production bundles exist (Forge: a `package` attempt writes .vite/build).
// Usage: npm i -D playwright-core && node boot-check.mjs
import { _electron } from 'playwright-core';

const app = await _electron.launch({ args: ['.'] });
await app.firstWindow();
await new Promise((r) => setTimeout(r, 2000));
for (const w of app.windows()) {
  const title = await w.title();
  if (title !== 'DevTools') {
    // Forge's template auto-opens DevTools; firstWindow() can be that one
    await w.screenshot({ path: 'boot-check.png' });
    console.log(`BOOT OK title="${title}"`);
  }
}
await app.close();
```

Then LOOK at `boot-check.png`. A white or blank rectangle is a failed boot with extra steps. Interaction works the same way (verified: `await win.getByText('Send IPC').click()` on the electron-vite template made the main process print `pong`, observed via `app.process().stdout`). Do NOT point `_electron.launch` at a packaged `.app` binary via `executablePath` — that hung here and printed a bare Node version banner; verify a packaged app by running its binary directly (`./out/<name>-darwin-arm64/<name>.app/Contents/MacOS/<name> &`, confirm the process stays alive, kill it).

## Never hand-write

- Forge: `forge.config.ts` skeleton, `vite.main.config.ts`, `vite.preload.config.ts`, `vite.renderer.config.ts` — all come from `create-electron-app`. Tuning the generated `forge.config.ts` (makers, fuses, plugins) afterward is normal and expected.
- electron-vite: `electron.vite.config.ts`, `electron-builder.yml`, `eslint.config.mjs`, `.prettierrc.yaml`, the three-file `tsconfig` set — all come from the creator. Same rule: edit after generation, never type from memory.
- Both: `package-lock.json`, and everything under `node_modules/electron/dist/` — the Electron binary tree is owned by electron's install script (or by the manual-unzip recovery in Traps, which exists precisely because that script can fail).
- `out/` (electron-vite build output, Forge package output) and `.vite/` (Forge build output) — generated, git-ignored by the scaffolds' own `.gitignore`.

## Thorough setup checklist

1. Decide Forge vs electron-vite before scaffolding (see Choosing); moving between them later means regenerating the config spine.
2. Scaffold with the exact command from the table — including `yes '' |` for electron-vite, or it dies on a hidden prompt in any scripted run.
3. electron-vite: `npm install`, then `npm install-scripts ls` and approve `electron` and `esbuild` — npm 11 skipped their install scripts with only a warning.
4. Bump `electron` to latest (`npm install --save-dev --save-exact electron@latest`). This is not optional polish: on Node 26 the old pin's installer is silently broken (Traps).
5. Forge only: plan the tooling modernization (TS 4.5 → current, `.eslintrc.json` → flat config) as an immediate follow-up; the template will not do it for you.
6. Run the boot check above and inspect the screenshot before writing any feature code.
7. Verify packaging once, early: electron-vite `npm run build:unpack`; Forge — on Node 26 this requires the Forge 8 alpha (Traps), so prove it now, not at ship time.
8. Commit the generated `.gitignore` and `package-lock.json` as-is. Forge already ran `git init`; electron-vite did not — `git init` yourself.

## Traps

**The electron-vite creator prompts even when you pass `--template`, and dies non-interactively.** Reproduced: `npm create @quick-start/electron@latest <dir> -- --template svelte-ts < /dev/null` reached "Add Electron updater plugin?" and exited 1. A finite `printf '\n\n\n' |` also died, at the second prompt ("Enable Electron download mirror proxy?"). Only a continuous default-accepting stream worked: `yes '' | npm create @quick-start/electron@latest <dir> -- --template svelte-ts` completed and scaffolded. There is no `--help` either — any flag-less invocation just starts prompting for a project name. (Recorded run: 2026-08-30, `@quick-start/create-electron@1.0.30`.)

**npm 11's install-scripts gating silently skips Electron's binary download.** `npm install` on the electron-vite scaffold printed only a warning (`packages have install scripts not yet covered by allowScripts`) and left `node_modules/electron` with no binary. What happens next depends on the Electron version: **electron 44 self-heals** — the first `npx electron` / app launch prints `Downloading Electron binary...` and proceeds (its `index.js` re-runs the installer on demand); **electron 39 just throws** `Electron failed to install correctly, please delete node_modules/electron and try installing again`. Manage it deliberately: `npm install-scripts ls` to see what was gated, `npm install-scripts approve electron` (and `esbuild`) to allow. The approval is written into the project's own `package.json` as an `allowScripts` map keyed by exact version (`"electron@39.8.10": true`) — so it stays local to the repo, and a later version bump needs a fresh approve (or electron 44+'s self-heal covers it). Forge's own scaffold-time install hit the same gating; its electron 44 self-healed on first launch. (Recorded runs: 2026-08-30, npm 11.19.0.)

**On Node 26, `extract-zip@2.0.1` fails SILENTLY — the process exits 0 mid-extraction with the promise never settling — and two important things sit on top of it.** Isolated repro on this machine: extracting a known-good Electron zip (integrity-verified with `unzip -t`) via `extract-zip` wrote one file and exited 0 with neither resolve nor reject firing. Consequences, both reproduced:
- **electron ≤39's `install.js` cannot install the binary at all**, even run by hand — exit 0, `dist/` containing a single stray `LICENSES.chromium.html`, no `path.txt`. electron 44+ is immune: its installer lazy-loads `@electron-internal/extract-zip`, a native-binding extractor (the swap references electron/electron#52481). This is why "bump electron to latest" is step 4 of the checklist, not cosmetics.
- **`electron-forge package` / `make` on Forge 7 produces NOTHING and exits 0.** Forge 7 pins `@electron/packager@18`, which still uses `extract-zip`. The run stops after `❯ Finalizing package` with no error, no `out/` directory, exit code 0 — a silent failure that looks like success in CI. `DEBUG='electron-packager'` shows the last act is `Extracting <electron zip> to <temp dir>`. A `package.json` override forcing `@electron/packager@^20` onto Forge 7 does NOT work — packager 20 changed hook signatures and Forge 7 crashes with `TypeError: done is not a function`. What DID work, verified end-to-end: upgrade every `@electron-forge/*` devDependency to the `alpha` dist-tag (8.0.0-alpha.10, which pins packager `^20`) with `npm i -D --legacy-peer-deps @electron-forge/cli@alpha @electron-forge/plugin-vite@alpha <...the rest...>` (plain install hits ERESOLVE on the plugin's peer ranges) — after that, `npm run package` produced `out/forge-app-darwin-arm64/forge-app.app` and the packaged binary ran.
- **Manual recovery** for a wedged electron install, verified: `unzip -q ~/Library/Caches/electron/<sha>/electron-v<ver>-darwin-arm64.zip -d node_modules/electron/dist` then `printf 'Electron.app/Contents/MacOS/Electron' > node_modules/electron/path.txt`. The system `unzip` is unaffected.
(Recorded runs: 2026-08-30, Node v26.7.0. If the project can choose its Node, an LTS line predating this breakage is the calmer path for Electron packaging work — but that is a version pin, i.e. Christopher's call, not a default.)

**Forge's `vite-typescript` template ships stale dev tooling with the latest Electron.** Observed generated `package.json`: `electron@44.0.0` (exact, latest — good) next to `typescript@~4.5.4`, `eslint@^8` with a legacy `.eslintrc.json`, and `vite@^5`. An agent assuming "freshly scaffolded == current tooling" will hand-write ESLint-8-isms into a world where ESLint 10 is flat-config-only. The scaffold is the start, not the finish, here more than usually. (Recorded run: 2026-08-30, `create-electron-app@7.11.2`.)

**Forge's template auto-opens DevTools, so Playwright's `firstWindow()` may be the DevTools window.** Observed: `firstWindow()` returned title `"DevTools"`; the real window (`"Hello World!"`) was second in `app.windows()`. Iterate windows and skip `DevTools` (the boot-check snippet above does).

## AI and agent resources

Checked by direct HTTP status (curl, not summarization), 2026-08-30:

- **Electron Forge**: `https://www.electronforge.io/llms.txt` (200, full table of contents with per-page `.md` links) and `https://www.electronforge.io/llms-full.txt` (200). Individual docs pages are fetchable as markdown at the URLs the index lists (e.g. `https://www.electronforge.io/cli.md`).
- **Electron itself**: `https://www.electronjs.org/llms.txt` → 404, `llms-full.txt` → 404. No llms file as of this check — use Context7 (`/electron/forge`, or the core Electron docs) instead of guessing APIs.
- **electron-vite**: `https://electron-vite.org/llms.txt` → 404. Context7 has current docs at `/alex8088/electron-vite-docs` (that is where the template list and creator flags above were cross-checked before the live runs).
- No official Electron MCP server was found on any of the three sites at this check — don't invent one.
