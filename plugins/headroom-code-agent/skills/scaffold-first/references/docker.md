<!-- freshness verified=2026-08-21 baseline=2026-08-30 -->
# Docker scaffold-first reference (verified 2026-08-21, round 3)

Scope: `docker init`, the official `uv` Docker pattern, compose files, multi-stage builds, lockfile-based installs, and base image tag pinning.

## Official setup commands

**Agent path (primary) — build the Dockerfile directly from the official uv Docker pattern:**

| Command | What it generates | Source |
|---|---|---|
| `uv init` | Scaffolds a Python project: `pyproject.toml`, `.python-version`, `README.md`, and a `src/<project>/__init__.py` layout. This is the file the Dockerfile's dependency layer is built from. | Astral uv docs (`docs/guides/projects.md`, `docs/concepts/projects/init.md`) |
| `uv lock` | Creates or updates `uv.lock` from `pyproject.toml`. `uv sync` and `uv run` will also create/update it automatically if it's missing or stale. | Astral uv docs (`docs/concepts/projects/layout.md`) |
| `uv sync --no-install-project` (Dockerfile builder stage, before `COPY . .`) | Installs dependencies only, from the lockfile, before the app source is copied in — keeps the dependency layer cached separately from source changes. | Astral uv docs (`docs/guides/integration/docker/index.md`) |
| `uv sync --locked` (Dockerfile builder stage, after `COPY . .`) | Installs the project into the image exactly as the lockfile resolved it; fails the build (non-interactively, nonzero exit) instead of prompting if the lockfile is stale. | Astral uv docs (`docs/guides/integration/docker/index.md`) |
| `docker compose config` | Not a generator — it validates and renders the final, resolved compose configuration (merges `-f` files, expands shorthand, interpolates variables). Use `--resolve-image-digests` to pin every image reference to its digest, and `-q`/`--quiet` to just validate without printing. | [docs.docker.com/reference/cli/docker/compose/config](https://docs.docker.com/reference/cli/docker/compose/config/) |

There is no separate "docker compose init" command — write `compose.yaml` by hand following the same official pattern, or see the humans-at-a-terminal note below.

**Humans at a terminal only — `docker init`:**

`docker init` walks a person through a few questions, detects the language/framework, and writes `.dockerignore`, `Dockerfile`, `compose.yaml`, and `README.Docker.md` (supports Go, Node, Python, PHP+Apache, Java/Maven, Rust, ASP.NET Core, and a generic "Other" template). Its only documented flag is `--version` — there is no `--yes`, no non-interactive mode, and no answers file, so an agent that runs it directly will hang waiting on stdin. A human at a keyboard should just run it and answer the questions; it's the fastest correct starting point for a person. An agent should skip it entirely and use the uv Docker pattern above instead — see Traps for what a recorded run hit when it tried to force `docker init` through anyway. (Source: [docs.docker.com/reference/cli/docker/init](https://docs.docker.com/reference/cli/docker/init/).)

## Choosing

**`docker init` vs. hand-assembling the Dockerfile from the official uv pattern.** `docker init` is a real Docker feature and gets you a working `Dockerfile`, `compose.yaml`, `.dockerignore`, and `README.Docker.md` in one shot — but it is a terminal wizard with no flag to skip the prompts (Docker's CLI reference documents exactly one option for it, `--version`). A human at a keyboard should just run it and answer the questions; it's the fastest correct starting point. A coding agent should not — a recorded run needed a pty workaround that ate 9 minutes to fight an interactive tool, and the output still needed a manual fix (the generated `.dockerignore` drops `README.md`, which breaks the build if `pyproject.toml` declares a readme). For agent-driven work, skip `docker init` and build the Dockerfile straight from Astral's documented uv-in-Docker pattern instead — it's non-interactive, official, and already covers the multi-stage/lockfile/non-root shape `docker init`'s Python template would give you anyway.

**Single-stage vs. multi-stage build.** A single `FROM python:...` with `uv sync` in it is simpler to read but ships the compiler toolchain and uv's cache into production. Astral's docs pattern uses a `builder` stage that runs `uv sync --locked --no-install-project`, then (in the docs' own "Non-editable installs" example) a slim final stage that does `COPY --from=builder /app/.venv /app/.venv`. The linked `astral-sh/uv-docker-example` repo's `multistage.Dockerfile` takes a related but distinct approach: it copies the whole `/app` directory in one step instead — `COPY --from=builder --chown=nonroot:nonroot /app /app` — and sets `ENV PATH="/app/.venv/bin:$PATH"` rather than isolating just the venv path. That repo's `--chown=nonroot:nonroot` is also the actual source of the checklist's "chowns the copied app to a nonroot user" claim below. Multi-stage wins any time the image ships anywhere other than your own laptop — smaller image, smaller attack surface. (Source: https://docs.astral.sh/uv/guides/integration/docker/index.md "Non-editable installs" section; https://raw.githubusercontent.com/astral-sh/uv-docker-example/main/multistage.Dockerfile.)

**Tag pinning depth: version tag vs. digest.** Docker's best-practices doc is explicit that tags are mutable — "a publisher can update a tag to point to a new image" — so `python:3.12-slim-trixie` is safer than `python:latest` but still not frozen. Pin to a digest (`FROM python:3.12-slim-trixie@sha256:...`) when you need bit-for-bit reproducible or security-sensitive builds; that trades away automatic patch updates, so pair it with Renovate, Dependabot, or Docker Scout to bump the digest on a schedule rather than never. A version tag alone is the right default for most projects; digest pinning is the right call once reproducibility or supply-chain integrity actually matters.

**Where to add an MCP server for a project: Docker MCP Catalog vs. hand-installing a runtime.** If the server you need is one of the 300+ in Docker's MCP Catalog (`hub.docker.com/mcp`), pulling it as a signed container through a Profile + the MCP Gateway is less setup than installing and trusting an arbitrary runtime by hand — Docker's own docs frame this as the point of the Catalog. Reach for `docker/mcp-registry` only if you're the one publishing a new server into that Catalog, not for day-to-day consumption.

## Never hand-write

- **`uv.lock`** — the uv docs state directly: it "should be checked into version control ... It is automatically updated during `uv sync` or `uv run`, and should not be edited manually." Regenerate it with `uv lock` (or let `uv sync`/`uv add`/`uv remove` update it), never edit the TOML-like contents by hand.
- **The output of `docker compose config`** — this is a rendered/resolved artifact (fully expanded YAML, digest-pinned if you pass `--resolve-image-digests`). If you save it to a file for inspection, treat that file as a generated snapshot, not a thing to edit and feed back in.

Two files are commonly *mistaken* for "generated, don't touch" but are not — call this out explicitly to avoid over-blocking legitimate work:

- **`Dockerfile` and `compose.yaml` from `docker init`** — these are a starting scaffold, not a machine-owned artifact. Docker's own docs describe `docker init` as producing "sensible defaults" that you are expected to open and adjust (ports, build commands, service names, environment variables) for your actual app. Hand-editing them after generation is the intended workflow, not a violation of scaffold-first — the rule that matters here is: run `docker init` first (as a human) to get the correct skeleton, then edit it, rather than hand-writing the skeleton from memory.
- **`pyproject.toml`** — scaffolded by `uv init`, but you're expected to hand-edit it afterward (add dependencies via `uv add`, or edit directly) as your project's dependency list evolves.

## Thorough setup checklist

- [ ] `Dockerfile`, `compose.yaml`, `.dockerignore`, and `README.Docker.md` exist and are either the output of `docker init` (run by a human at a terminal) or hand-assembled by an agent following the official uv Docker pattern — either way, none of these were hand-authored from a remembered template.
- [ ] For Python projects, `uv init` was run before any Dockerfile was written, so `pyproject.toml` and `.python-version` exist and are real, not guessed.
- [ ] `uv.lock` exists, is committed to version control, and was produced by `uv lock`/`uv sync` — never opened and edited by hand.
- [ ] The Dockerfile uses a **multi-stage build**: a builder stage that installs dependencies and builds the app, and a slim final stage that copies in only the built artifacts (venv, binary, etc.) — not the build toolchain.
- [ ] Dependency installation inside the image is **lockfile-based and reproducible**: for Python/uv this means `uv sync --locked` (fails loudly if the lockfile is out of date) rather than an unpinned `uv sync` or `pip install -r requirements.txt` with no hash pinning.
- [ ] The dependency-install layer is cached separately from the app-source-copy layer (copy `pyproject.toml`/`uv.lock` first, sync with `--no-install-project`, then `COPY . .` and sync again) so code changes don't invalidate the dependency cache.
- [ ] **Base images are pinned**, not left on a floating tag. At minimum a specific version tag (e.g. `python:3.12-slim-trixie`, not `python:3-slim` or `python:latest`); for reproducible/security-sensitive builds, pin by digest too (`FROM alpine:3.21@sha256:...`), understanding this trades away automatic patch updates unless paired with an update bot (Renovate/Dependabot) or Docker Scout.
- [ ] `.dockerignore` excludes `.venv`, `.git`, local env files, and other build-irrelevant content — and if it came from `docker init`, it's been checked against `pyproject.toml`'s `readme` field (see Traps), not just assumed correct.
- [ ] `compose.yaml` was validated with `docker compose config` (or `docker compose config -q`) before being trusted, rather than assumed correct because it parses.
- [ ] The image runs as a non-root user in the final stage (the official uv example chowns the copied app to a `nonroot` user) rather than defaulting to root.

## Traps

**`docker init` has no non-interactive mode.** The command's only documented flag is `--version`; every other input comes through terminal prompts, and there is no answers-file or `--yes` equivalent. An agent that tries to run it directly will hang waiting on stdin. A recorded test run (2026-08-21) had to drive it through a pty to get past this, and that workaround alone took 9 minutes — for agent-authored Dockerfiles, skip `docker init` and use the official uv Docker pattern directly instead. (Source: https://docs.docker.com/reference/cli/docker/init/ — only `--version` is documented; recorded test run 2026-08-21.)

**`docker init`'s generated `.dockerignore` can break the very build it scaffolded.** In a recorded test run (2026-08-21), the `.dockerignore` that `docker init` wrote excluded `README.md` — which broke the `uv sync` build because the project's `pyproject.toml` declared a `readme` field that pointed at the now-ignored file. Anyone who does run `docker init` for a Python project should check `.dockerignore` against `pyproject.toml`'s `readme` setting before trusting the build. (Source: recorded test run 2026-08-21.)

**`docker init`'s scaffold assumes a listening server.** The templates it generates (including the Python one) are written around an app that binds a port and serves requests — a recorded test run found this assumption baked in, which doesn't fit batch jobs, CLIs, or worker processes without manual rework of the generated `Dockerfile`/`compose.yaml`. (Source: recorded test run 2026-08-21.)

**Floating base-image tags are a moving target, not a pin.** `python:3.12`, `node:20`, and `latest` are all mutable pointers — Docker's build best-practices doc states plainly that "a publisher can update a tag to point to a new image," so a rebuild next week can silently pull a different image than the one that was tested. Use a specific version tag at minimum, and a digest pin for anything reproducibility-sensitive. (Source: https://docs.docker.com/build/building/best-practices/.)

**`uv.lock` edited by hand desyncs from the resolver.** The uv docs say the lockfile "is automatically updated during `uv sync` or `uv run`, and should not be edited manually." A hand-edit produces a lockfile whose hashes don't match what the resolver would actually produce; `uv sync --locked` will then either fail the build (the safe outcome) or, if the edit happens to look consistent, silently ship different dependency versions than intended. (Source: Astral uv docs, `docs/concepts/projects/layout.md`.)

**Trusting `compose.yaml` because `docker compose up` didn't error.** Compose's interpolation and multi-file merge logic can silently resolve to something other than what you intended without ever throwing an error at `up` time. `docker compose config` (optionally with `-q` to just validate, or `--resolve-image-digests` to pin every image reference) renders the fully resolved configuration so merge and interpolation mistakes surface before deploy. (Source: https://docs.docker.com/reference/cli/docker/compose/config/.)

**Hand-writing a Dockerfile from memory instead of following the official pattern.** This produces plausible-looking but non-standard structure (wrong base image choice, missed `.dockerignore` entries, no multi-stage split) that following Astral's documented uv Docker pattern — or, for a human at a terminal, `docker init` — gets right by default. (Source: https://docs.astral.sh/uv/guides/integration/docker/index.md; https://docs.docker.com/reference/cli/docker/init/.)

**Installing dependencies without a lockfile constraint.** Plain `pip install -r requirements.txt`, or `uv sync` with no `--locked`/`--frozen`, inside the image lets a rebuild silently resolve different transitive versions than what was tested. (Source: https://docs.astral.sh/uv/guides/integration/docker/index.md — `uv sync --locked` "asserts the lockfile is up to date.")

**Single-stage builds that ship the build toolchain.** Compilers, uv's cache, and dev dependencies bloat the production image's size and attack surface when they ride along in a single-stage build. The official pattern is a builder stage plus a slim final stage that only copies the built `.venv`/artifacts across with `COPY --from=builder`. (Source: https://docs.astral.sh/uv/guides/integration/docker/index.md.)

**Copying the whole project before installing dependencies.** This busts the Docker layer cache on every source change. The official uv pattern copies only `pyproject.toml` and `uv.lock` first, syncs with `--no-install-project`, and only then copies the rest of the source. (Source: https://docs.astral.sh/uv/guides/integration/docker/index.md.)

## AI and agent resources

Docker publishes a real, first-party set of resources for coding agents — not just human docs. All of the following were verified live on 2026-08-21.

- **Docs llms.txt / llms-full.txt** — `https://docs.docker.com/llms.txt` is a structured index of Docker's documentation, built for AI consumption; `https://docs.docker.com/llms-full.txt` is the full text corpus. Fetch the index before nontrivial Docker work; pull the full corpus if you need everything at once (RAG, offline indexing).

- **Official docs MCP server** — `https://mcp-docs.docker.com/mcp`. A real MCP server (confirmed by a live protocol handshake — it identifies as `llms-txt` v1.26.0) that exposes a `fetch_docker_docs` tool. Use this instead of raw HTTP fetches when your harness talks MCP.

- **Docker MCP Catalog and Toolkit** — `https://docs.docker.com/ai/mcp-catalog-and-toolkit/`, catalog browsable at `https://hub.docker.com/mcp`. Docker's system for running 300+ verified MCP servers as signed containers, organized into shareable Profiles and routed through an MCP Gateway to clients like Claude Code and Cursor. This is where you go to add a new MCP server to a project without hand-installing its runtime.

- **docker/mcp-registry** (GitHub) — the official submission pipeline: a PR here gets a server built, signed, and published into the Catalog above. Relevant if you're publishing a new MCP server on Docker's platform, not for runtime use.

- **Docker Hub MCP Server** — docs at `https://docs.docker.com/docker-hub/mcp-server/`, source at `https://github.com/docker/hub-mcp`. Lets an agent search and manage Docker Hub images and repos by natural language instead of guessing tags.

- **Docker Agent** — `https://docs.docker.com/ai/docker-agent/` (source: `https://github.com/docker/docker-agent`). Docker's own open-source multi-agent builder/runtime, bundled into Docker Desktop 4.63+; agent teams are YAML configs run with `docker agent run` and packaged as OCI artifacts.

- **AGENTS.md in docker/docker-agent** — `https://github.com/docker/docker-agent/blob/main/AGENTS.md`. Docker uses the vendor-neutral AGENTS.md convention on its own flagship agent repo: minimal comments, a required `task build` / `task test` / `task lint` pass before calling work done, a rule that older versioned config schemas are frozen, and Conventional Commits with signed commits. Read this before editing that repo.

- **Docker Sandboxes** — `https://docs.docker.com/ai/sandboxes/`, with a supported-agents list at `.../ai/sandboxes/agents/` covering Claude Code, Codex, Copilot, Cursor, Docker Agent, Droid, Gemini, Kiro, OpenCode, and Shell (an agent-less sandbox option). Use this when a coding agent needs to run isolated from the host, with credentials and tool access scoped to the sandbox.

No rumors were included — every resource above was fetched or otherwise directly confirmed rather than assumed from training data.
