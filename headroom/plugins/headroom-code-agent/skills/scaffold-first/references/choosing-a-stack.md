<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
# choosing-a-stack: the intake interview (added 2026-08-30)

Before any new application is scaffolded, run this interview. Its job: gather what the idea actually is, then match each piece of it to the best-fit stack — where "best fit" may only name technologies with a verified reference page in this skill. Anything else is surfaced as a known gap that needs a verified pass first, never recommended on vibes.

## Hard rules

1. **Interview via AskUserQuestion, always** — Christopher's standing rule. Never dump open questions as prose or a markdown doc.
2. **Staged, not one giant form.** AskUserQuestion takes at most 4 questions per call with 2–4 options each. Run 2–3 rounds; later rounds depend on earlier answers, so don't pre-plan them rigidly.
3. **Skip what's already answered.** If the request already pins a surface or constraint, don't re-ask it — confirm it in the restatement instead.
4. **One ruling per surface.** The output names a stack per surface (web app, mobile app, desktop app, API/backend, ML/data, docs), each with a one-line reason and a pointer to its reference page — delivered per the house output rules (decision artifact when it's a real decision, with the chat message staying short).
5. **Recommend only what's verified.** The menu below is the whole menu. If the best fit for a piece is something off-menu (e.g. a game engine, an embedded target), say so plainly, name it as a gap, and offer a verified pass on it as the next step.
6. **Fixed choices stay fixed** — Linear for tracking, Figma for componentized design, Postgres for the database. Don't re-open them in the interview. Hosting is not this interview's decision at all (current direction: GCP over Railway long-term, nothing ruled) — never ask about it here.

## Round 1 — the idea

Ask (adapting wording to what's known):
- **What is it?** (one sentence of purpose — free text via "Other" is fine)
- **Who uses it?** (internal team / customers / both / just Christopher)
- **Where does it run?** (multiSelect: web browser / phone / desktop / server-only)
- **What's the data story?** (none-or-light / shared database / heavy offline-first)

## Round 2 — per-surface constraints (only for surfaces picked)

- Phone picked: **native feel & device APIs** — does it need deep native features (camera, background tasks, widgets) or is it a normal app UI? And is app-store distribution required, or is a web app on a phone acceptable?
- Desktop picked: **footprint & backend language** — is install size / memory a real constraint (points at Tauri) and is there appetite for Rust, or keep everything JS (points at Electron)?
- Web picked: **rendering needs** — content/marketing site vs interactive app vs full-stack app with server routes.
- Server picked: **shape** — REST/GraphQL API, background workers, ML pipeline.
- Always useful when unclear: **team/agent constraint** — one shared codebase across surfaces (points at Flutter/KMP/Compose or RN+web sharing) vs best-per-surface.

## Round 3 — tie-breakers (only if two stacks still tie)

Ask the single question that splits them (examples: "Svelte house stack or React ecosystem for this one?", "Share UI across platforms or share logic only?"). If nothing ties, skip round 3.

## The verified menu (the only recommendables)

| Surface | Options with verified pages | Default lean |
|---|---|---|
| Web app / site | SvelteKit (`sveltekit.md`) · Next.js (`nextjs.md`) · TanStack Start / React Router (`react-fullstack.md`) · SolidStart / Qwik (`solid-qwik.md`) · Angular (`angular.md`) · Astro (`astro.md`) · Vue/Nuxt (`js-web-extended.md`) | SvelteKit (house stack); Astro for content-heavy sites; TanStack Start when React + end-to-end type safety is the ask; Qwik when low-end-device startup is the hard requirement; Angular for enterprise-convention teams |
| Mobile app | Expo/React Native (`js-web-extended.md`, `react-native.md`) · Flutter (`flutter.md`) · Kotlin Multiplatform (`kotlin-multiplatform.md`) | Expo for JS-team speed; Flutter for one-codebase UI fidelity; KMP when sharing logic with native UI matters |
| Desktop app | Electron (`electron.md`) · Tauri (`tauri.md`) | Tauri for lean installs + Rust backend; Electron (electron-vite/Svelte) to stay all-JS |
| API / backend | FastAPI, Django (`python-web.md`) · Hono, Fastify, NestJS (`js-servers.md`) · Go (`go.md`) · Rails, Laravel (`rails-laravel.md`) · Spring Boot, ASP.NET Core (`spring-dotnet.md`) | FastAPI for Python APIs; Hono for edge/serverless targets; Fastify for a plain fast Node API; Go for small fast single-binary services; Nest, Django, Rails, or Laravel when batteries-included wins (pick by team language); Spring or .NET for enterprise-convention shops |
| ML / data | Kedro, ZenML, DVC, Dagster, MLflow, W&B (`ml-pipelines.md`) | per that page |
| Scraping | Scrapy/Crawlee/Playwright (`scrapers.md`) | per that page |
| Library/CLI | uv (`python-uv.md`) · JS/TS core (`js-ts-core.md`) · Rust (`rust.md`) · Swift (`swift.md`) | match the ecosystem it serves |
| Docs site | Mintlify (`docs-mintlify.md`) | Mintlify |
| UI components | shadcn / shadcn-svelte (`shadcn.md`) · Lit web components (`preact-lit.md`) | per frontend choice; Lit when components must outlive any one framework |
| Tiny/embedded UI | Preact (`preact-lit.md`) | when bundle size is the hard constraint |

Cross-surface note: when web + mobile + desktop are ALL picked and one codebase is the priority, weigh Flutter (all four targets, one Dart codebase) and Compose Multiplatform (Kotlin, wizard targets android/ios/desktop/web) against per-surface picks — the table's per-surface defaults assume surfaces chosen independently.

## After the ruling

Deliver the per-surface rulings (decision artifact if it's a genuine decision; wait for Christopher's confirmation via AskUserQuestion when options are close). Then, and only then, proceed to step 1 of the main procedure: templates check, official scaffold commands from each chosen stack's reference page.
