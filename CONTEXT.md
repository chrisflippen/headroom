# Headroom

Headroom cuts the tokens an AI coding agent spends by shrinking what the model sees
(tool output, repeated content, noisy payloads) while keeping the original recoverable.
It runs as a proxy in front of the model API, as a set of MCP tools, or both.

## Language

### Compression

**CCR (Compress, Cache, Retrieve)**:
The core idea: shrink content before the model sees it, keep the original under a
hash, and hand the original back on request.
_Avoid_: caching layer, compression pipeline

**Marker**:
The short in-context stand-in left where content was compressed away. Carries the
hash needed to get the original back.
_Avoid_: placeholder, stub, token

**Retrieval**:
Getting the original content back from a marker's hash.
_Avoid_: decompress, expand

**Crusher (SmartCrusher)**:
The compressor that keeps the first, last, error, and unusual items of a large tool
output and drops the rest.
_Avoid_: truncator, sampler

**Kompress**:
The aggressive, lossy compressor that runs after the lossless ones when that mode
is turned on.

**Cross-turn dedup**:
Replacing a later, word-for-word repeat of earlier tool output with a pointer back
to the first copy. Only happens inside the proxy.
_Avoid_: dedupe, repeat folding

**Waste signal**:
A named category of avoidable tokens found in a request: JSON bloat, HTML noise,
base64 blobs, repeated whitespace, dynamic dates, repetition, re-reads.
_Avoid_: waste type, inefficiency

**Shaper (output shaper)**:
Logic that trims or reformats a model response before it goes back to the caller.

### Accounting

**Ledger**:
The durable, append-only log of every compression event, from both the proxy and
the MCP tools. What the savings report reads.
_Avoid_: telemetry log, stats file

**Bucket**:
One aggregated line of savings numbers (tokens before, tokens saved, cost, calls)
for one category and time window.
_Avoid_: bin, group

**Beacon**:
The opt-out, anonymous, content-free usage summary sent to Headroom Labs. Separate
from local telemetry.
_Avoid_: telemetry (that means the local kind), phone-home

### Deployment

**Profile**:
A named install configuration: which supervisor runs headroom, which port, and so
on. Resolved from the current folder or an explicit flag.
_Avoid_: environment, config set

**Deployment manifest**:
The saved record of one profile's install choices.
_Avoid_: install state, lock file

**Workspace root**:
The read-write folder where headroom keeps state (ledger, caches, manifests).

**Config root**:
The read-mostly folder where headroom keeps user configuration. Distinct from the
workspace root.
_Avoid_: home dir, dotfolder

### Agents

**Registrar**:
The per-agent adapter that knows how to add or remove an MCP server in that agent's
config (Claude Code, Codex, OpenCode, Grok).
_Avoid_: installer, integration

**Context tool (retired)**:
The old third-party binaries (`rtk`, `lean-ctx`) that rewrote shell commands and
replaced the Read tool. Removed; only cleanup code remains.

### Code agent

**Code agent**:
Headroom's own Claude Code agent: the main model may only reach files and databases
through headroom's tools, never the built-in Read, Edit, Write, Grep, or Glob.
_Avoid_: wrapper agent, woz agent

**Agent switch**:
The setting that makes the code agent the default for a session. Written by wrap
into user settings; a project setting can override it.
_Avoid_: default agent flag, agent override

**Managed entry**:
A settings entry that headroom wrote, carries headroom's marker, and unwrap will
remove. Anything without the marker is the user's and is left alone.
_Avoid_: injected setting, owned entry

**Helper**:
A cheaper model the code agent hands work to: a scan helper for lookups, an edit
helper for writing code.
_Avoid_: subagent (too general), worker

**Brief**:
The short interpretation shown under the user's prompt before the code agent acts:
the goal, what is not the goal, likely files, and skills to run. The prompt itself
is never changed.
_Avoid_: enhanced prompt, rewritten prompt, tuning

**Unchanged marker**:
The one-line stand-in the search tool returns instead of a file's text, when the
caller passes back the stamp from its own earlier read and that stamp still matches.
A kind of marker.
_Avoid_: cache hit, skipped file

**Stamp**:
A short hash of a file's bytes, returned with every read and every edit. The caller's
proof that it already holds that exact version of the file — there is no server-side
cache of file content, so a stamp only means something coming from its own earlier
read or write.
_Avoid_: cache key, version hash, etag

**Connection reference**:
The per-project pointer in headroom config to a database connection string held in
the system keychain. The config never holds the secret itself.
_Avoid_: stored connection, DSN entry
