<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: @angular/cli | npm view @angular/cli version | 22.1.7 -->
# angular scaffold-first reference (researched 2026-08-30)

Covers **Angular** via its official CLI. Verified live this session (macOS arm64, Node v26.7.0, Angular CLI 22.1.6): app scaffolded non-interactively, dev server booted and hit, the scaffold's own tests run and passing. Angular is the enterprise SPA lane; the house default for new web apps remains SvelteKit.

## Official setup commands (all run live)

| Step | Command | Notes |
|---|---|---|
| Scaffold | `npx @angular/cli@latest new <dir> --defaults --skip-git --package-manager npm` | `--defaults` suppresses every prompt (verified, stdin closed). Standalone components, routing, and the modern builder are the defaults now. |
| Dev server | `npm start` (`ng serve`) | :4200 |
| Test | `npm test -- --watch=false` | see the Vitest correction below |
| Add artifacts | `npx ng generate component\|service\|route <name>` | the CLI owns boilerplate; never hand-copy a component skeleton |
| Update | `npx ng update` | Angular's own migrator — use it for version bumps, never hand-edit versions |

Boot check that worked here: `npm start` → curl on :4200 returned the app HTML served through Vite. `npm test -- --watch=false` → 2 tests passed untouched.

## Stale-training corrections (verified live)

- **Angular 22 tests run on VITEST, not Karma/Jasmine.** The scaffold has no karma.conf; `ng test` boots Vitest. Karma-era flags fail loudly: `--browsers=ChromeHeadless` errors asking for a `@vitest/browser-*` provider (that flag now selects a Vitest browser-mode provider, not a Karma browser). Plain `--watch=false` is the CI form.
- **The dev server is Vite-based** — the served HTML carries `/@vite/client`. Old webpack-era assumptions (custom webpack configs, `ng eject` lore) don't apply.
- **`ng new` defaults are standalone + signals-era Angular** — no NgModule boilerplate in the scaffold. Don't add an `app.module.ts` from memory.

## Never hand-write

- `angular.json` — created by `ng new`, modified by `ng add`/`ng generate`/`ng update`; edit values, never author it.
- Component/service/route boilerplate — `ng generate` owns it.
- The `tsconfig.json`/`tsconfig.app.json`/`tsconfig.spec.json` triple — generated; extend in place.

## Thorough setup checklist

1. Scaffold with `--defaults --skip-git --package-manager npm`; add `--style=scss` or `--ssr` up front if wanted (re-check `ng new --help` for current flags rather than trusting memory).
2. Boot check (`npm start` + curl :4200) and `npm test -- --watch=false` green before feature work.
3. All new artifacts through `ng generate`; framework/library additions through `ng add <pkg>` where the package supports it.
4. Version bumps only through `ng update`.
5. `git init` yourself when using `--skip-git`; wire test + build into CI per `ci-github-actions.md`.

## Traps

**Karma-era test flags are a false friend.** `npm test -- --watch=false --browsers=ChromeHeadless` fails with "requires @vitest/browser-playwright..." — the same flag name changed meaning between runners. Drop `--browsers` unless you're deliberately setting up Vitest browser mode. (Observed 2026-08-30, Angular CLI 22.1.6.)

**First `ng serve` compile takes noticeably longer than the Vite-instant feel suggests** — the curl check needs ~20s of patience on first boot, not 5. (Observed 2026-08-30.)

## AI and agent resources

- `https://angular.dev/llms.txt` → 200 (checked 2026-08-30) — fetch before nontrivial work.
- `ng new --help`, `ng generate --help` are the live authority on flags; the CLI's help output is accurate.
