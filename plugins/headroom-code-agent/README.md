# Headroom code agent

A Claude Code plugin that replaces the default coding agent with headroom's own. The
code agent never touches the built-in Read, Edit, Write, Grep, or Glob tools — every
file look-up and change goes through headroom's `Search`, `Edit`, and `Sql` tools
instead, so headroom can shrink what the model sees before it lands in context.

## What it installs

- **`code`** — the main agent. Does normal coding work (read, change, run tests) but
  reaches files and databases only through `Search`, `Edit`, and `Sql`.
- **`scan`** — a cheap, read-only helper (Haiku) the code agent hands broad look-ups
  to, so a big grep or a "where is this used" sweep doesn't burn the main agent's
  context. Returns short findings, never file dumps.
- **`edit`** — a helper (Sonnet) the code agent hands already-decided, mechanical edits
  to: renames, repeated small fixes, the same change across many files. Runs the tests
  it's told to run and reports back a diff summary and exit codes.
- Two skills: `pyrefly-autofix` (fixing type-checker errors) and `scaffold-first`
  (setting up a new repo, tool, or framework the right way from the start).
- Hooks: on session start, it makes sure the code agent's skills are up to date; on
  every prompt, it adds a short brief under the prompt showing what the agent
  understood before it acts. After every edit, it runs whichever type checker and
  linter the edited file's own project has configured. Any findings are reported
  back to the agent, which must fix them before touching another file.

## Turning it on

`headroom wrap claude` writes the managed setting that makes the code agent the
default agent for a session. It only touches entries headroom itself wrote — anything
you set yourself is left alone.

## Removing it

Run `headroom code-agent remove`. That removes the managed setting and switches the
session back to Claude Code's own default agent and tools.
