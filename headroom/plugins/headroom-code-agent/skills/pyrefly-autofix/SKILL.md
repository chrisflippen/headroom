---
name: pyrefly-autofix
description: Fix pyrefly (or any CLI type-checker) errors reliably and at scale — deterministic AST-level fixes for mechanical error classes, a tight edit-then-reverify loop (the svelte-autofixer discipline) for everything else, and a hard rule against treating suppression comments or existing repo config as proof a pattern is correct. Use whenever a workflow needs to burn down a large list of type-checker errors.
---

# pyrefly-autofix: classify first, fix mechanically where you can, verify in the same breath as every edit

## Why this exists

A 2026-08-29 workflow ran 705 Haiku agents, one per pyrefly error, then
verified in a *separate* later wave with *different* agents rerunning pyrefly
per directory cluster. The fixer agents self-reported "fixed" for most
errors; the real number was much lower (112 of 720 fixed), and the gap only
surfaced because a Fable gate re-ran pyrefly directly at the very end and
caught the mismatch. Two root causes, both fixed by this skill:

1. **Verification was decoupled from the edit** in both time and agent
   identity, so a wrong self-report could survive two whole waves before
   anything grounded it in the real tool.
2. **Every error was treated as a judgment call requiring an LLM**, when a
   pass over the actual 720-error corpus showed **~55% (393 errors) follow a
   small number of fully deterministic, mechanically-describable patterns** —
   the same class of error class-level generic-type-argument gap, the same
   `Indexed()`-in-annotation-position mistake, the same missing lambda
   parameter annotation, repeated hundreds of times across the codebase.
   Spending a Haiku agent's judgment on each occurrence of a pattern that a
   script (or a five-line codemod) could apply everywhere at once is waste,
   and worse, it's 393 extra chances for an LLM to guess wrong on something
   that has exactly one correct mechanical answer.

## Rule zero: existing code and existing docs are not proof of correctness

**Never treat "this repo already does X" or "CLAUDE.md already documents X"
as evidence that X is the right fix.** A codebase mid-refactor, or one this
skill is actively cleaning up, can have its own stale documentation
rationalizing bad patterns that were never revisited. Before applying any
fix — mechanical or judgment-based — check it against the tool's *current*
own documentation (Context7 / the pyrefly/Beanie/pydantic docs themselves),
not against what a neighboring file or a project doc already does. A prior
agent documenting a pattern as the house convention does not make it the
correct one.

**Suppression is never an acceptable fix. Not `# type: ignore`, not scoped
`# type: ignore[code]`, not `# noqa`, not `cast(Any, ...)` used to silence
rather than narrow, not any other mechanism that makes the checker stop
reporting an error without the underlying type actually being correct.**
Every error gets a real fix: the correct annotation, a genuine narrowing
check, a signature change, `Annotated[...]`, restructuring the code so the
types are actually sound. If an agent cannot find a real fix after its
bounded attempts, that is a `blocked` result that goes to escalation or gets
surfaced to Christopher — it is never resolved by suppressing the check.
Strict mode means strict enforcement; a suppressed error is a hidden error,
not a fixed one.

## Step 1 — classify every error before touching an agent budget

Run the checker once, parse every error into `{file, line, col, code,
message}`, then bucket by `code` (or by a regex over `message` when the code
is too coarse, e.g. Beanie-specific vs generic `bad-argument-type`). For each
bucket, decide:

- **Mechanical (script-fixable, zero LLM calls):** the fix is a pure
  syntactic transform derivable from the error alone — no file-specific
  reasoning needed. Write it as a small Python script using `ast`/`libcst`
  or even careful regex, run it once across every occurrence, then re-run
  the checker to confirm the bucket is empty. Examples seen in this repo's
  720-error corpus (verify against current docs before trusting this list on
  a different pyrefly/Beanie/pydantic version):
  - `implicit-any-type-argument` on a bare generic (`list`, `dict`, `Redis`)
    where the concrete element/key/value type is provable from a nearby
    assignment or the field's declared model type — often mechanical, but
    check: if the "obvious" type requires reading unrelated code to confirm,
    it has drifted into judgment territory, reclassify it.
  - `Function call cannot be used in annotations` — Beanie's `Indexed()`
    called directly in annotation position (`field: Indexed(str)`). Current
    Beanie/pydantic convention is `Annotated[str, Indexed()]` (PEP 593) — a
    real structural fix, not a comment. Confirm the exact current signature
    via Context7 before mass-applying — Beanie's `Indexed` API has changed
    across major versions.
  - `implicit-any-lambda` — the lambda parameter's type is provable from the
    function it's passed to (e.g. a `sorted(..., key=lambda x: x.foo)` where
    the iterable's element type is known) — add the annotation mechanically;
    if the calling context is too dynamic to prove a type, it's judgment.
  - `missing-override-decorator` — mechanical: add `@override` (import from
    `typing`) wherever a method already correctly overrides a parent and
    pyrefly has already confirmed the signature matches.
  - `implicit-any-empty-container` — mechanical when the container's later
    usage (an `.append(x)` a few lines down, a return-type annotation) pins
    the element type unambiguously; otherwise judgment.
- **Judgment (LLM-required):** the fix genuinely depends on understanding
  what the code is trying to do — `bad-argument-type` where a `None` is
  flowing somewhere it shouldn't (is the right fix a narrowing check, a
  signature change, or a real bug?), `missing-attribute` (does the model
  actually need that field, or is the caller wrong?), `bad-assignment`
  against a `Literal` union (is the runtime value actually one of those
  literals, or is the type too narrow?). These go through Step 3.

Log the bucket sizes and the mechanical/judgment split in the workflow's
plan packet — this is exactly the kind of arithmetic a Gate A pass should be
able to sanity-check (a script author claiming "mechanical" for a bucket
that actually needs per-file reasoning is a real failure mode; the gate
should spot-check a sample of the "mechanical" bucket's task specs same as
any other step).

## Step 2 — apply mechanical fixes as a script, not a swarm

Write one script per mechanical bucket. Run it against every file in the
bucket in one pass. Re-run the checker immediately after — the whole bucket
should go to zero (or reveal that some occurrences were misclassified and
belong in the judgment bucket instead; move them and continue). Zero agent
calls spent on a pattern a script can apply everywhere at once.

## Step 3 — judgment errors: tight edit-verify loop, one agent per file

For everything left, use the same mechanism the Svelte MCP's
`svelte-autofixer` uses — a real MCP tool the agent calls, not a shell
command the agent is merely instructed to run. This repo ships one:
`.claude/mcp-servers/pyrefly-checker/server.py`, registered in `.mcp.json`
as the `pyrefly-checker` server, exposing `pyrefly_check(path)` and
`pyrefly_check_full()`. Both run the actual `pyrefly` CLI as a subprocess on
disk and return structured JSON (`{clean, error_count, errors[], raw_stderr}`)
— there is no path from "the agent claims it ran the checker" to a false
positive, because the check IS the tool call, not a narrated side effect of
one. (Needs a Claude Code session restart after `.mcp.json` changes before
the tool is actually reachable — same as any new MCP server.)

The SAME agent that makes the edit calls `mcp__pyrefly-checker__pyrefly_check`
again immediately, reads the real diagnostics, and keeps looping — edit,
call the tool, edit again — until the tool itself reports `clean: true`.
There is no separate "verifier" agent and no self-report to trust; the loop
only ends when the tool says so, inside the same context that made the edit.

1. **One agent owns one file** (or a small cluster of errors within one
   file) for its full loop — never split "make the edit" and "confirm the
   edit" across two different agent calls, two different waves, or two
   different files running concurrently against the same file.
2. **The agent's own last action before returning must be calling
   `mcp__pyrefly-checker__pyrefly_check` with the exact file it just
   edited** (path relative to `apps/api`, e.g. `"app/main.py"`). Loop: edit
   → call the tool → still `clean: false`? → edit again → call the tool
   again, up to a small bounded attempt count (3–4) before returning
   `blocked`. If the MCP tool is unreachable for some reason, fall back to
   running `uv run --python 3.14 pyrefly check <exact file>` directly via
   Bash and parsing its real output — never fall back to self-report.
3. **The returned status is the checker's own output, not the agent's
   narration.** Schema: `{status: 'clean' | 'still_failing' | 'blocked',
   remaining_errors: [...], blocked_reason}` — `remaining_errors` is what the
   checker printed on the agent's last run, verbatim, not a summary.
4. **Never suppress. Ever.** Find the real fix — correct annotation,
   narrowing, `Annotated[...]`, a small signature change, restructuring so
   the types are actually sound. If the checker seems wrong about a
   construct the language allows, that is a signal to re-read the current
   docs and reconsider the code's shape, not a license to silence the
   check. An error that can't be genuinely fixed within the attempt budget
   is `blocked`, not suppressed.
5. **A second, independent agent still re-verifies at the batch level**
   before anything is presented as done — this pattern makes false "fixed"
   claims far rarer, not impossible. Keep the independent re-check; just
   stop relying on it as the *only* check.
6. **Batch size stays small** (~20–50 errors per workflow wave) so a bad
   batch is cheap to catch and redo, instead of discovering the miss rate
   after 700+ agents have already run.

## Workflow-script shape

```js
async function fixFileToClean(file, errors) {
  for (const err of errors) {
    let attempt = 0
    let clean = false
    let lastErrors = null
    while (attempt < 3 && !clean) {
      attempt++
      const r = await agent(
        `Fix this pyrefly error in ${file}: ${err.code} at line ${err.line}: ${err.message}. ` +
        `Find the real fix — correct type, narrowing, Annotated[...], a signature change. ` +
        `Never suppress: no # type: ignore, no # noqa, no cast(Any, ...) to silence rather than narrow. ` +
        `If you cannot find a real fix, return status "blocked", never a suppressed one. ` +
        `Then call the mcp__pyrefly-checker__pyrefly_check tool with path "${file}" and report its real result — do not guess, do not narrate success, do not use Bash to run pyrefly yourself unless the tool call errors.`,
        { model: 'haiku', effort: 'low', schema: FIX_AND_VERIFY_SCHEMA }
      )
      clean = r.status === 'clean'
      lastErrors = r.remaining_errors
    }
    if (!clean) return { file, err, status: 'failed', lastErrors }
  }
  return { file, status: 'clean' }
}
```

Run these per-file chains in `parallel()` across files (never within a file —
concurrent edits to one file race). Follow with one independent full-scope
recheck before calling the batch done, per point 5 above.

## When NOT to reach for the agent loop

If a bucket looked mechanical in Step 1 but the script's re-run shows
leftover errors in that bucket, don't force it — reclassify those specific
occurrences as judgment and route them through Step 3. If the checker has no
exact-scope invocation (can't check just one file or one function), the
tight loop still helps but the "own last action" step gets noisier — say so
in the plan rather than pretending it's as tight as a single-file check.
