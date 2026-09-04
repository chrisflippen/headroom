<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
# cloud-railway scaffold-first reference (researched 2026-08-30)

Covers **Railway** — today's hosting (long-term direction is Google's cloud, per the 2026-08-20 ruling; nothing ruled on the destination yet, so Railway remains where things deploy). Everything marked *run live* below was executed this session against Christopher's real account. Cloud pages follow a stricter rule than framework pages: **reads are free game; anything that creates or deploys billable resources needs Christopher's explicit go in the moment — every time.**

## The agent paths (checked live)

Two Railway integrations exist in the environment, and only one was authenticated at this check:

- **The claude.ai Railway connector** (tools like `whoami`, `list-projects`, `list-services`, `get-logs`, `set-variables`, `create-service`, `railway-agent`) — WORKED: whoami returned the account, projects and services listed cleanly. This is the agent path.
- **The local `railway` MCP server and the local `railway` CLI (5.41.3 installed)** — both returned `Unauthorized` (no valid `RAILWAY_TOKEN` in the environment). Don't assume the CLI works; check `railway whoami` first, and prefer the connector.

## What agents do freely (reads — run live)

- `whoami` — confirm which account you're acting on before anything else.
- `list-projects` / `list-services` / `list-deployments` / `list-variables` (names, not values, unless needed) / `get-logs` / `get-service-config` / metrics tools.
- `search-docs` / `fetch-docs` for Railway's current documentation.

Observed live: two workspaces (Digital 1 Group, Digital 1 Clients), ten projects including the dev-operations stack (postgres, grafana, loki, mongo, mysql services).

## What needs Christopher's explicit go (billable / state-changing)

- `create-project`, `create-service`, `deploy`, `deploy_template`, volumes, TCP proxies, domains.
- `set-variables` on a real service, `restart-service`, `redeploy`, `scale_service`.
- Anything on the dev-operations project — that stack runs real infrastructure.

Ask with the exact resource named, get the yes, then act. A "throwaway" service still consumes usage the moment it deploys; there is no free verification deploy.

## Deploying an app (the shape, per current docs — deploy itself gated)

1. Repo-connected service: `connect-service-source` to a GitHub repo; Railway builds with Nixpacks/Railpack from the repo automatically.
2. Config-as-code: `railway.json`/`railway.toml` in the repo tunes build/deploy — start from Railway's docs template via `search-docs`, not memory.
3. Set variables via the connector; reference variables (`add_reference_variable`) link services.
4. Domains: `generate-domain` for a railway.app subdomain (gated: it exposes the service publicly).
5. TanStack Start scaffolds take `--deployment railway` at create time (`react-fullstack.md`) — the one framework in the standard with first-party Railway wiring.

## Traps

**Two Railway tool surfaces, opposite auth states.** The connector was live while the CLI and local MCP were both Unauthorized — an agent that grabs the first Railway tool it sees can conclude "no access" wrongly, or worse, split state between surfaces. Run the connector's `whoami` first and stick with one surface per session. (Observed 2026-08-30.)

**`MONGO_URL` errors right after a compose/backend start are startup lag, not failure** — wait and retry (standing note from the 2026-08-17 ruling, kept here because Railway-hosted backends show the same pattern).

## AI and agent resources

- The connector's `search-docs`/`fetch-docs` — Railway's own docs, current, queryable. Use them instead of memory for railway.json shapes and Railpack behavior.
- The `railway-agent` tool exists for genuinely complex multi-service investigations — it acts on the real account, so the billable-gate rule applies to what you ask it to do.
