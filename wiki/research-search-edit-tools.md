# Research: adding Search and Edit MCP tools to headroom

Primary-source notes for whoever designs the new `Search` (find files + return
contents in one call, with an "unchanged since last read" marker and
tree-sitter structure summaries) and `Edit` (batch find/replace across many
files) MCP tools, plus a Claude Code agent that bans the built-in
Read/Edit/Write/Grep/Glob tools. Every claim below is cited as `path:line`.
Where a claim comes from a README/wiki doc instead of code, it says so and
notes whether the code backs it up.

**Docs note up front:** `docs/content/docs/*.mdx` (a Fumadocs/Next.js site,
`docs/README.md:1-11`) is the real published docs site — `wiki/mcp.md` has a
matching `docs/content/docs/mcp.mdx`. But the task said to write here anyway,
so this file lives in `wiki/` as instructed. There is no `wiki/README.md`.

## Summary

Headroom already ships an MCP server (`headroom mcp serve`) with three tools —
compress, retrieve, stats — plus a fourth, `headroom_read`, that is a working
prototype of exactly the "unchanged since last read" idea, but it's off by
default and only caches whole files in memory. A Rust-backed search-result
compressor and a 12-language tree-sitter code compressor already exist and
should be reused rather than rebuilt. The Claude Code plugin here ships hooks
only — no agent, no MCP registration — so a banned-built-in-tools agent and
the MCP server installation are two separate, currently-unconnected
mechanisms. The biggest fact for the designer: headroom used to ship almost
exactly this (a Bash-rewriting hook plus a Read-replacing tool, via two
third-party binaries called `rtk` and `lean-ctx`) and **removed it**, because
the binaries had no signature verification, the install path had gaps, and
uninstalling left durable junk on disk. That removal (and its cleanup code)
is required reading before building a first-party version of the same idea.

## 1. MCP server(s)

There are **two different things** that both look like "the MCP server":

- `headroom/integrations/mcp/server.py:1-559` is **not** a server Claude Code
  talks to. It's a reusable library (`HeadroomMCPCompressor`,
  `compress_tool_result()`, `HeadroomMCPClientWrapper`,
  `create_headroom_mcp_proxy()`) that a host application can use to compress
  the output of *someone else's* MCP tools (Slack, GitHub, a database, etc.)
  before it hits the model. It ships tool-name-pattern profiles for common
  servers (`DEFAULT_MCP_PROFILES`, `headroom/integrations/mcp/server.py:102-138`)
  and runs `SmartCrusher` under the hood (`server.py:262-278`). Not relevant
  to building new tools, but relevant if the new Search/Edit tools' own
  output ever needs the same treatment.

- `headroom/ccr/mcp_server.py:1-1199` is the **actual** server registered
  into Claude Code. It uses the official `mcp` Python SDK's v1 low-level API:
  `from mcp.server import Server`, `from mcp.server.stdio import
  stdio_server`, `from mcp.types import TextContent, Tool`
  (`mcp_server.py:54-56`). `pyproject.toml:81-83` pins `mcp>=1.28.1,<2.0.0`
  specifically **because** this server still uses the old decorator API and
  hasn't been ported to SDK 2.x yet (tracked as GH #2658, regression #2977).
  A new tool added here inherits that same v1-API constraint.

  Tools registered today (`mcp_server.py:73-76`, `640-706`):
  - `headroom_compress` — compress arbitrary content on demand.
  - `headroom_retrieve` — fetch original content by hash.
  - `headroom_stats` — session compression stats.
  - `headroom_read` — **feature-flagged off by default**
    (`HEADROOM_MCP_READ` env var, `mcp_server.py:80-88`; only added to the
    tool list when `_READ_ENABLED` is true, `mcp_server.py:673-704`).

  `headroom_read` is described in its own tool description as a drop-in
  replacement for the built-in Read tool ("Use this INSTEAD of the built-in
  Read tool for significant token savings", `mcp_server.py:679-684`) and its
  handler (`_handle_read`, `mcp_server.py:944-1043`) is the closest existing
  thing to the requested if-modified-since feature — see section 4.

  **Launch**: CLI command `headroom mcp serve` (`headroom/cli/mcp.py:329-395`),
  stdio transport by default, or `--transport http --host 127.0.0.1 --port
  8788 --path /mcp` for Streamable HTTP. HTTP transport is built from the
  MCP SDK's `StreamableHTTPSessionManager` wrapped in a Starlette app, served
  by uvicorn (`headroom/ccr/mcp_http.py:1-79`). **No auth flag exists** on
  `headroom mcp serve`; the only protection is that the default bind host is
  `127.0.0.1` (loopback). If a designer adds an HTTP-transport option for the
  new tools, note there's no existing auth pattern to copy — it doesn't
  exist yet.

  **Registration into Claude Code**: `headroom mcp install`
  (`headroom/cli/mcp.py:120-183`) calls `install_everywhere()` in
  `headroom/mcp_registry/`, which picks a per-agent `Registrar`. For Claude
  Code, `ClaudeRegistrar` (`headroom/mcp_registry/claude.py:1-40`) prefers
  writing via the `claude` CLI (`claude mcp add/remove/list/get`) when it's
  on PATH, and falls back to reading/writing `~/.claude.json` directly
  (Claude Code 2.x) or the older `~/.claude/mcp.json` (older Claude Code /
  Claude Desktop) when it isn't. The registration payload is a
  `ServerSpec(name, command, args, env)` dataclass
  (`headroom/mcp_registry/base.py:44-55`), serialized per-agent format by
  `_spec_to_entry` (`claude.py:342-351`).

  **Retrieval fallback**: `headroom_retrieve` checks the MCP server's own
  local `CompressionStore` first (`mcp_server.py:477`, returns
  `"source": "local"`), and if the hash isn't found there, calls out over
  HTTP to a running proxy at `HEADROOM_PROXY_URL` (default
  `http://127.0.0.1:8787`, `mcp_server.py:90, 502, 544-566, 825-915`) —
  because the MCP server is a separate process from the proxy and each has
  its own in-memory store. I confirmed this live: reading a file with the
  `Read` tool in this research session produced a compression marker with a
  `hash=`, and `mcp__headroom__headroom_retrieve` on that hash returned
  `"source": "local"` with the byte-identical original text.

  **Savings accounting**: both `headroom_compress` calls and proxy requests
  append one line each to a shared, file-locked JSONL ledger
  (`headroom/savings_ledger.py:1-15`) so a new tool's compressions show up in
  `headroom savings` without any extra wiring, as long as it goes through
  the same store/ledger calls.

There is also a **third**, unrelated MCP server:
`headroom/memory/mcp_server.py` (own `Tool(...)` registrations at lines
50-74, `main()` at line 427) for the separate hierarchical-memory feature. It
is not wired into `headroom mcp serve` (no cross-reference found either
direction) and is out of scope here, but worth knowing it exists so a new
Search/Edit MCP server module doesn't get confused with it.

## 2. Claude Code plugin

`plugins/headroom-agent-hooks/` contains exactly four files:
`.claude-plugin/plugin.json`, `.github/plugin/plugin.json` (apparent Copilot
CLI variant), `hooks/hooks.json`, `README.md`. No `agents/`, no `skills/`,
no `.mcp.json`.

- `plugin.json` declares `name: "headroom"`, `version: "0.37.0"` (matching
  `pyproject.toml`'s `version = "0.37.0"`, line 7), and points `homepage`/
  `repository` at `headroomlabs-ai/headroom` (the upstream, not this fork).
- `hooks/hooks.json` registers two hooks, both running the literal shell
  command `headroom init hook ensure`, timeout 15s: a `SessionStart` hook
  matching `startup|resume`, and a `PreToolUse` hook matching `Bash|
  PowerShell`.
- **`${CLAUDE_PLUGIN_ROOT}` is not used anywhere in this plugin.** The hook
  command has no path interpolation at all — it assumes a global `headroom`
  binary is already on `PATH` (installed separately via `uv tool install` /
  `pip install headroom-ai[proxy]`, not shipped inside the plugin). This is
  different from a plugin that bundles its own binary/bundle.js and resolves
  it relative to `${CLAUDE_PLUGIN_ROOT}` — there's no code path here to copy
  for that pattern.
- `.claude-plugin/marketplace.json` at the repo root lists exactly this one
  plugin entry, `source: "./plugins/headroom-agent-hooks"`.

**Bottom line for the designer**: this plugin does *not* register the MCP
server and does *not* ship an agent. MCP registration is `headroom mcp
install`, a completely separate CLI command with its own config-file writes
(section 1). If the plan is "one plugin that both bans built-in tools via an
agent definition *and* wires up the new MCP tools," that plugin doesn't
exist yet in any form — it would need a new `agents/` directory (Claude Code
agent definition files) and probably a `.mcp.json` inside the plugin, neither
of which this repo currently has any example of to model off.

## 3. Hooks

Two different "hooks" exist in this codebase and they are unrelated:

- `headroom/hooks.py:1-151` — `CompressContext`, `CompressEvent`,
  `CompressionHooks` (line 41, 56, 73). This is a Python **SDK**-level
  middleware/callback system for `HeadroomClient` users (compress/decompress
  event callbacks in application code). It has nothing to do with Claude
  Code hooks.
- The Claude-Code-facing hook logic lives behind the CLI command invoked by
  `hooks.json`: `headroom init hook ensure`
  (`headroom/cli/init.py:1096-1125`, hidden `init hook` group). It:
  1. Resolves a **profile** name: explicit `--profile`, else a CWD-derived
     local profile if a manifest exists for it, else the global profile
     `"init-user"` (`_GLOBAL_PROFILE`, `init.py:54`, `_local_profile`,
     `init.py:115-125`).
  2. Calls `_ensure_profile_running(profile)` (`init.py:738-767`), which
     loads a `DeploymentManifest` (`headroom/install/models.py:90`, loaded
     via `headroom/install/state.py:81 load_manifest`) for that profile, and
     if it isn't already ready (`wait_ready`, 1s probe), starts it: as a
     persistent Docker container, an OS service supervisor (launchd on
     macOS / systemd on Linux), or a detached background process, depending
     on `manifest.preset` / `manifest.supervisor_kind` (`init.py:759-764`).
  3. All of this is best-effort and swallows exceptions (`init.py:742-743,
     766-767`) — a corrupt manifest or a supervisor failure must never crash
     a Claude Code session start or a Bash call.

So "the hook" doesn't inject prompt text or intercept tool calls itself — it
just makes sure the durable proxy/runtime process for the active profile is
up, on every session start and before every Bash/PowerShell call. A new
Search/Edit MCP tool wouldn't need to touch this at all unless it needs its
own background process kept alive the same way.

## 4. Existing capabilities to reuse

This is the most load-bearing section for the design. Four things already
exist and should be starting points, not references:

**(a) Cross-turn verbatim dedup — tracks "has the model already seen this?"
today.** `headroom/transforms/cross_turn_dedup.py:1-306` is a pure-stdlib,
deterministic module that answers exactly the question in the task prompt:
"is there anything that already tracks what file content the model has
already seen." It runs on the **proxy's** request pipeline, not per-tool: it
looks at every tool-output block in the whole conversation window and
replaces a later block's span with an in-context pointer
(`[↑NL same as msg M: 'anchor']`, `_pointer()`, lines 121-143) when that span
already appeared verbatim earlier — including tolerating a uniform
line-number shift so a `cat -n` re-read after an edit still folds
(`_num_and_key`, lines 64-83; `_longest_match`, lines 168-223). It's
prefix-monotonic on purpose (`is_prefix_monotonic`, lines 291-306) so
provider-side prompt caching isn't invalidated. It's wired into
`ContentRouter._cross_turn_dedup_messages`
(`headroom/transforms/content_router.py:5885-5988`) and the OpenAI
Responses-API path (`headroom/proxy/handlers/openai.py:1252-1287`), gated by
`enable_cross_turn_dedup` (default `False`,
`content_router.py:1551`) / env var `HEADROOM_DEDUPE=1`
(`content_router.py:1542`).

Important limits for the designer: this only fires **inside the proxy**, on
the full message list of a single outbound request, and it's opt-in and off
by default. It does not help a Claude Code session running MCP-only (no
`ANTHROPIC_BASE_URL` pointed at the proxy) — see section 9. It also folds
*any* tool output, not just file reads (grep output, `git diff`, anything).
A new Search tool's own if-modified-since check is a different, complementary
mechanism: it can avoid ever *emitting* the bytes a second time (saving
context-window space in the live conversation, not just upload bytes),
whereas cross-turn dedup only trims what's sent to the provider after the
fact.

**(b) `headroom_read` — an already-built (but disabled) prototype of the
requested Search feature's caching half.** Covered in section 1
(`mcp_server.py:944-1043`). Read it carefully before designing the new tool:
it hashes file bytes with sha256, keeps an in-memory `dict[str, tuple]` cache
of `path -> (content_hash, ccr_hash, line_count, token_estimate)`
(`mcp_server.py:1042` sets this), and on a repeat read of an unchanged file
returns a small JSON status object instead of the content, telling the model
to call `headroom_retrieve` if it actually needs the bytes again
(`mcp_server.py:1004-1025`). It exposes a `fresh: bool` argument explicitly
because the in-memory cache is **lost across subagents and context
compaction** (`mcp_server.py:692-698` — the tool description says so
directly). It has no glob support, no regex, no line-range slicing, and no
tree-sitter summary mode — the new Search tool is a superset of this, not a
replacement, but the caching mechanics (hash-compare, CCR-store, "call
retrieve if you need it back") are a template to copy.

**(c) A Rust-backed search-results compressor already parses `rg`/`grep -n`
output.** `headroom/transforms/search_compressor.py:1-40` states plainly it
is now "a thin shim" over `crates/headroom-core/src/transforms/
search_compressor.rs` — the real implementation moved to Rust in what the
comments call "Phase 3e.2." It keeps public dataclasses `SearchMatch`,
`FileMatches`, `SearchCompressorConfig`, `SearchCompressionResult` for
backward compatibility (lines 6-9) and documents specific bug fixes the Rust
port carries: correct handling of Windows drive-letter paths in grep output
and filenames containing dashes (lines 18-29). A new Search MCP tool that
shells out to ripgrep (or reimplements glob+regex search) should feed raw
results through this existing scorer/selector rather than write a second
parser for the same `file:line:content` format.

**(d) A 12-language tree-sitter code compressor already exists — reuse its
parser plumbing for "structure summary" mode.**
`headroom/transforms/code_compressor.py` (2,647 lines) defines `CodeLanguage`
(`code_compressor.py:319-333`): Python, JavaScript, TypeScript, Go, Rust,
Java, C, C++, Perl, C#, PHP — already wider than the WOZCODE Search tool's
TS/JS-only "summary" mode this task is modeled on. It manages a
**thread-local** `tree_sitter.Parser` per language (`_get_parser`,
`code_compressor.py:215-282`) and guards against `tree-sitter-language-pack`
not being installed at all (`_check_tree_sitter_available`,
`_tree_sitter_importable`, lines 67-102), since `tree-sitter` is behind its
own optional extra, not bundled with `[proxy]` (see section 5/6). It also
has an open circuit-breaker pattern per language
(`_syntax_breaker_open`/`_record_syntax_outcome`, lines 169-214) that trips
off parsing for a language after repeated failures — useful precedent for a
new tool that must never crash on a weird file. `pyproject.toml:104-113`
explicitly pins `tree-sitter-language-pack>=0.10.0,<1.0` because 1.0+
switched to an incompatible node API (`.kind` vs `.type`, callable
`root_node`) that this file's node-walk code depends on (tracked as issue
#1216) — a new tool using tree-sitter needs to respect the same pin, not
add a second dependency spec.

**Not found / needs its own work:** no existing glob-matching utility
surfaced in this pass (only regex/tree-sitter compressors were found — a new
Search tool likely needs its own glob implementation or a small dependency).
`headroom/storage/` (`headroom/storage/__init__.py:1-11`) is a generic
pluggable key-value/event storage layer (sqlite/jsonl backends) for the SDK
— not related to file-content caching. `headroom/memory/` is a large,
separate subsystem (vector search over long-term semantic memories,
`headroom/memory/__init__.py:1-15`) — a different concern from "has this
exact file changed since I last read it" and should not be conflated with
it. `headroom/shared_context.py:1-24` (`SharedContext`) rides the same
CCR compress/store/retrieve pipeline to move large content cheaply between
agents (`ctx.put()` / `ctx.get(..., full=True)`) — relevant if the new
tools' output needs to be handed to a subagent later.

## 5. Rust side

Workspace layout (`Cargo.toml:1-20`, `RUST_DEV.md`):
`crates/headroom-core` (shared types + the `Transform` trait surface),
`crates/headroom-proxy` (the proxy binary), `crates/headroom-simulators`
(deterministic local upstream stub for tests), `crates/headroom-py` (PyO3
cdylib exposing `headroom._core`, built via maturin —
`crates/headroom-py/src/lib.rs`), `crates/headroom-parity` (fixture-based
Python-vs-Rust parity test runner). Build: `[build-system]` in
`pyproject.toml:1-3` uses `maturin>=1.5,<2.0` as the build backend, with
`[tool.maturin]` (`pyproject.toml:403-408`) set to `python-source = "."` so
the wheel picks up everything under `headroom/`.

The convention observed (not documented as a hard rule, inferred from what
exists): a transform is written in Python first, and **some but not all**
transforms get ported to Rust later for the hot path, following a recorded
fixture/parity pattern — `tests/parity/fixtures/<transform>/*.json` holds
Python outputs recorded by `recorder.py`, and `headroom-parity`'s `parity-run`
CLI checks the Rust port produces byte-identical results
(`RUST_DEV.md`, `make test-parity` target). `search_compressor.py` is a
concrete example of this: the Python module now explicitly says it's a thin
shim over the Rust crate (section 4c). `code_compressor.py` (2,647 lines,
still pure Python at the module level, though `crates/headroom-core/src/
transforms/code_compressor.rs` exists too) suggests code compression is
mid-port. Given this, a brand-new Search/Edit tool should almost certainly
start in **Python** (fast to iterate, matches where MCP tool handlers
already live in `headroom/ccr/mcp_server.py`) and only move logic into Rust
later if profiling shows it's hot — same path `search_compressor` took.

Makefile targets confirming the split (`Makefile`): `make test` = `cargo
test --workspace`, `make test-parity` = builds `headroom-py` via maturin then
runs `parity-run run`, `make build-wheel` = `maturin build --release -m
crates/headroom-py/Cargo.toml`.

## 6. Conventions

- **Package layout**: flat top-level modules under `headroom/` (config.py,
  paths.py, hooks.py, ...) plus subpackages by concern (`ccr/`, `transforms/`,
  `mcp_registry/`, `memory/`, `telemetry/`, `install/`, `cli/`, `cache/`,
  `proxy/`).
- **Tests**: `tests/` mirrors the subpackage structure (`tests/test_transforms`,
  `tests/test_mcp_registry`, `tests/test_cache`, etc.), 808 `test_*.py` files
  found (`find tests -name "test_*.py" | wc -l`). `[tool.pytest.ini_options]`
  is declared in `pyproject.toml:501` (not inspected in detail here — flag
  for the designer to check fixture/marker conventions before adding tests).
- **Lint/type**: `ruff` (`pyproject.toml:429-452`, target py310, line-length
  100, select E/W/F/I/B/C4/UP, double-quote/space format) and `mypy`
  (`pyproject.toml:456-500`, `disallow_untyped_defs = true` globally, with
  explicit per-module override lists that turn it off for dynamically-typed
  modules — notably `headroom.integrations.mcp` and `headroom.ccr.mcp_server`
  are already in that untyped-OK override list, `pyproject.toml` around line
  462-475). **No `pyrefly` config exists anywhere in this repo** — grepped
  `pyproject.toml`, `Makefile`, `CONTRIBUTING.md` and found nothing; don't
  assume it's part of the toolchain.
- **Makefile**: `make test`, `make test-parity`, `make bench`,
  `make build-proxy`, `make build-wheel`, `make verify-rust-core`, `make fmt`
  / `fmt-check`, `make ci-precheck` (runs `ci-precheck-rust`,
  `ci-precheck-python`, `ci-precheck-commitlint` together), `make
  install-git-hooks`.
- **Commits/CONTRIBUTING**: `CONTRIBUTING.md:76-95` — `make install-git-hooks`
  installs pre-commit checks, commitlint on commit messages, and
  `ci-precheck` on push. Title format is **Conventional Commits**
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `CONTRIBUTING.md:83`).
  `CONTRIBUTING.md`'s opening table is blunt about scope creep: refactor-only
  and test/CI-only PRs are "Don't," and any new feature or architectural
  change should be an issue or a Discord discussion **first**, before a PR.
  A "ban built-in tools, add two new MCP tools" change is squarely an
  architectural change by this repo's own definition.
- **Docs**: `docs/content/docs/*.mdx` is the real published site
  (Fumadocs — `docs/README.md`); `wiki/*.md` is a flat folder of longer
  architecture/reference notes with matching filenames to some doc pages
  (e.g. `wiki/mcp.md` ↔ `docs/content/docs/mcp.mdx`) but no `wiki/README.md`
  or index. `wiki/filesystem-contract.md` (see section 7) and
  `wiki/ARCHITECTURE.md` are the closest things to a contributor architecture
  guide.
- **CHANGELOG**: `CHANGELOG.md:1` — Keep a Changelog format, Semantic
  Versioning. Root version is `0.37.0` (`pyproject.toml:7`), matching the
  plugin's `0.37.0` (`plugins/headroom-agent-hooks/.claude-plugin/plugin.json:3`)
  and the marketplace metadata version.

## 7. Configuration and paths

`headroom/paths.py:1-60` documents (and the code implements) a **two-root
model**, restated more fully in `wiki/filesystem-contract.md:8-19`:

- `HEADROOM_CONFIG_DIR`, default `~/.headroom/config` — read-mostly,
  user/admin config (model catalogs, plugin settings).
- `HEADROOM_WORKSPACE_DIR`, default `~/.headroom` — read-write runtime state
  (savings, logs, memory DB, telemetry, caches).

Resolution precedence for any per-resource path (`filesystem-contract.md:21-36`):
explicit argument → per-resource env var (e.g. `HEADROOM_SAVINGS_PATH`) →
derived from the canonical root → hardcoded default. `paths.py:145-231`
implements `workspace_dir()`, `config_dir()`, and per-resource getters
(`savings_path`, `settings_path`, `toin_path`, etc.) following this exact
order (`_resolve()`, `paths.py:125-145`).

**For a new tool's own state/settings**, there's an existing plugin-author
API meant for exactly this: `paths.plugin_config_dir("my-tool")` →
`~/.headroom/config/plugins/my-tool` and `paths.plugin_workspace_dir(
"my-tool")` → `~/.headroom/plugins/my-tool` (`wiki/filesystem-contract.md:90-103`
documents the Python API; names containing `/` or `\` are rejected to keep
the namespace flat, `filesystem-contract.md:114-115`). A new Search/Edit
tool's file-hash cache (the if-modified-since state) should almost certainly
persist here instead of only in-memory like `headroom_read` does today,
which is the exact limitation section 4b flags (state lost across subagents
and compaction).

`settings_store.py` (1,036 lines) was not read in depth this pass — flagged
as a follow-up if the new tools need user-editable settings beyond the
plugin-dir convention above.

## 8. Telemetry

Two independent switches, documented directly in
`headroom/telemetry/beacon.py:1-21`:

- `HEADROOM_TELEMETRY` — **off by default, opt-in.** Local-only aggregation
  for the in-process collector and `/stats` / `/v1/telemetry` endpoints;
  never leaves the machine. Enabled only by an explicit on-value
  (`is_telemetry_enabled()`, `beacon.py:31-45`, fail-closed: unset/unrecognized
  = disabled).
- `HEADROOM_BEACON` — **on by default, opt-out.** Uploads an anonymous,
  content-free session summary to Headroom Labs. Disable with
  `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1` (the cross-tool convention,
  honored — `beacon.py:9-11, 66-84`), or offline mode.
- A third, separate destination: `HEADROOM_OTEL_METRICS_*` sends operational
  metrics to a user-controlled OpenTelemetry collector that Headroom Labs
  never sees (`beacon.py:15-17`).

If the new MCP tools should be counted in telemetry/beacon the way proxy
requests and `headroom_compress` calls are, follow the ledger pattern in
section 1 (append to `savings_ledger.py`'s shared JSONL) rather than
inventing a new counting path.

## 9. Things that would surprise a designer

**(a) headroom already built almost exactly this feature once, as a
third-party integration, and removed it.** `git log` on this repo shows
commit `e0ce4b1d` ("fix: remove rtk and lean-ctx CLI context tools (#2677)")
and a preceding `5829602f` ("refactor!: remove rtk and lean-ctx CLI context
tools"). Full commit message (`git show e0ce4b1d`): headroom used to
download two **third-party** binaries, `rtk` ("Rust Token Killer") and
`lean-ctx`, register a Claude Code `PreToolUse` hook that rewrote Bash
commands to go through `rtk`, and inject marker-fenced instructions into
agent hint files telling models to "prefer `ctx_read` over `Read`" — i.e.
the exact shape of what this task asks for (a Bash-wrapping hook plus a
Read-replacing tool), except via a downloaded third-party binary instead of
a native headroom MCP tool. It was removed because:
  - the binary download had **no SHA or signature verification**, only
    `rtk --version` as a smoke test (commit message, "Also worth noting"
    paragraph);
  - the install path had real gaps — `scripts/install.sh` and `install.ps1`
    ran `rtk init --global --auto-patch` directly from shell, bypassing the
    Python feature gate entirely (commit message table, row 1);
  - `wrap openhands` was broken by the gate in a way 8 tests hid because
    they all mocked the binary path (commit message table, row 2);
  - removing the code wasn't enough — the hooks, binaries, and injected
    instruction text are **durable state on the user's disk** that survives
    a headroom upgrade, so `headroom/context_tool_cleanup.py:1-40` exists
    solely to find and remove what earlier versions installed, run on every
    `wrap`/`unwrap`, deliberately idempotent and conservative (only deletes
    files/hooks it can prove it owns).

  This should shape the new design directly: ship it as a first-party
  in-process MCP tool (no downloaded binary, no signature-verification gap),
  avoid a hook that silently rewrites Bash commands (that's exactly the
  `rtk` pattern that broke `wrap openhands`), and if the new agent definition
  or MCP registration is ever removed later, plan the uninstall path
  up front rather than after the fact — copy the
  idempotent-and-conservative posture of `context_tool_cleanup.py`, not the
  original rtk installer.

**(b) MCP tool output and the proxy's compression are two separate,
overlapping systems that don't know about each other in real time.** A new
Search/Edit tool's output goes through whatever compression the tool code
itself calls (e.g. CCR store via `headroom_compress`-style logic). If the
user is *also* running `headroom proxy` with `ANTHROPIC_BASE_URL` pointed at
it, that same tool output — once it's part of the request Claude Code sends
upstream — gets a **second** pass through the proxy's `ContentRouter`
(cache alignment, cross-turn dedup if enabled, etc.), because from the
proxy's point of view an MCP tool result is indistinguishable from a
built-in tool result in the Anthropic message format. Conversely, if the
user runs the MCP tools **without** the proxy (`wiki/mcp.md:1-19` pitches
this as "no proxy required"), cross-turn dedup (section 4a) never runs at
all, since that code only lives in the proxy's request path. A new tool's
own if-modified-since caching (section 4b's `headroom_read` pattern) is the
only savings mechanism that works in both configurations — worth stating
explicitly in the design, since it's the reason `headroom_read` exists as
its own thing instead of just relying on cross-turn dedup.

**(c) `headroom mcp install` and `headroom_read`'s feature flag are silent
gotchas for a new tool.** Registration happens once, manually
(`headroom mcp install`); a newly-added tool in `mcp_server.py` won't reach
users who installed before the change without them re-running that command
or the plugin's `hooks.json` re-triggering something (it doesn't — the
plugin hook only ensures the *runtime/proxy* is running, not that the MCP
tool list is current, section 3). And `headroom_read` shows the project's
own precedent for shipping a new tool **disabled by default** behind an env
var while it's unproven — worth deciding up front whether the new
Search/Edit tools should launch the same way.

## Glossary

Terms as the code actually uses them, one line each, with the file that
defines them:

- **CCR (Compress-Cache-Retrieve)** — headroom's core reversible-compression
  architecture: compress content, keep the original retrievable by hash.
  `headroom/ccr/__init__.py:1`.
- **Marker** — an in-context stand-in for content that was compressed away,
  resolved back to full content on demand via retrieval; see
  `headroom/ccr/marker_resolution.py`.
- **Beacon** — the opt-out, anonymous, content-free usage-summary upload to
  Headroom Labs; distinct from local `HEADROOM_TELEMETRY`.
  `headroom/telemetry/beacon.py:9-11`.
- **Waste signal** — a category of detected token waste in a request (JSON
  bloat, HTML noise, base64 blobs, repeated whitespace, dynamic dates,
  repetition, re-reads). `WasteSignals` dataclass,
  `headroom/config.py:821-835`. (This fork's active branch,
  `fix/waste-signal-buckets`, is literally about this.)
- **Bucket** — an aggregation of savings numbers (tokens saved, tokens
  before, cost, call count) for one category/time window in the savings
  ledger. `_Bucket` dataclass, `headroom/savings_ledger.py:228-244`.
- **Shaper** — output-shaping logic in the proxy that reformats/trims a
  response before it's returned. `headroom/proxy/output_shaper.py`.
- **Ledger** — the durable, append-only, file-locked JSONL log of every
  compression event (MCP and proxy), aggregated by `headroom savings`.
  `headroom/savings_ledger.py:1-15`.
- **Profile** — a named deployment configuration (which supervisor, which
  proxy port, etc.) tracked by a `DeploymentManifest` and resolved from CWD
  or an explicit flag; `"init-user"` is the global fallback profile.
  `headroom/cli/init.py:54, 115-125`; `headroom/install/models.py:90`.
- **Deployment manifest** — the persisted record of one profile's install
  choices, read by `load_manifest()`. `headroom/install/state.py:81`.
- **Cross-turn dedup** — the proxy transform that replaces a later verbatim
  repeat of earlier tool output with an in-context pointer.
  `headroom/transforms/cross_turn_dedup.py:1-27`.
- **SmartCrusher** — the transform that keeps first/last/error/outlier items
  from a large tool output and drops the rest, used by both the proxy and
  the generic MCP-compression library. Referenced at
  `headroom/integrations/mcp/server.py:60, 271-278`.
- **Kompress** — the aggressive, lossy, ML/heuristic compressor chained
  after lossless folds when `lossless_then_lossy` is on.
  `headroom/transforms/content_router.py:1552-1558`.
- **Workspace / Config root** — the two-root filesystem model
  (`HEADROOM_WORKSPACE_DIR` read-write state vs `HEADROOM_CONFIG_DIR`
  read-mostly config). `wiki/filesystem-contract.md:8-19`.
- **TOIN** — a telemetry JSON file tracked under the workspace root
  (`toin.json`); not expanded in this pass — flag for follow-up if telemetry
  wiring is needed. `headroom/paths.py` (`HEADROOM_TOIN_PATH_ENV`, line 51).
- **rtk / lean-ctx** — the two removed third-party CLI context tools
  discussed in section 9a. No longer present in this codebase except as
  cleanup logic (`headroom/context_tool_cleanup.py`) and CHANGELOG history.

## Open questions for the designer

1. **Reuse `headroom_read`'s pattern or replace it?** The new Search tool's
   if-modified-since feature overlaps completely with `headroom_read`
   (section 4b). Extend that handler (glob/regex/line-range/tree-sitter on
   top of it) or design fresh and eventually retire `headroom_read`? Either
   way its in-memory-only cache (lost per-subagent, per-compaction) should
   move to `paths.plugin_workspace_dir()` (section 7) so the cache survives.

2. **What does "ban the built-in tools" mean given this repo has zero
   existing agent definitions to model from?** `plugins/headroom-agent-hooks`
   has no `agents/` directory at all (section 2). Is a new agent definition
   file going in this plugin, a new plugin, or somewhere else? And should it
   register the new MCP server itself (a `.mcp.json` in the plugin) instead
   of relying on the separate manual `headroom mcp install` step?

3. **Given the rtk/lean-ctx removal (section 9a), what's the uninstall
   story from day one?** Should a new agent-definition + MCP-tool feature
   ship its own `purge_*` cleanup path up front, following
   `context_tool_cleanup.py`'s idempotent/conservative model, rather than
   retrofitting one after a future removal?

4. **Should the new tools work with no proxy running at all** (matching
   `headroom_compress`/`retrieve`/`stats`'s "no proxy required" pitch,
   `wiki/mcp.md:1-19`), or is proxy-required acceptable given cross-turn
   dedup (the closest existing "seen this already" mechanism) only runs
   inside the proxy (section 9b)?

5. **Ship enabled by default, or behind a flag like `headroom_read`
   (`HEADROOM_MCP_READ`)?** The project's own precedent is to ship new,
   unproven MCP tools disabled by default (section 9c).

6. **Glob matching**: no existing glob utility was found in this pass
   (section 4, "Not found"). Worth a second look specifically for that
   before writing new code, in case it exists somewhere this research
   missed — `headroom/fsutil.py` was not opened in this pass and could be
   worth checking first.

7. **HTTP-transport auth**: if the new tools are ever exposed over
   `--transport http` instead of stdio, there is currently no auth pattern
   anywhere in `headroom mcp serve` to copy (section 1) — that gap exists
   independent of this task, but a new tool shouldn't be the first thing
   exposed through it without deciding how to handle it.
