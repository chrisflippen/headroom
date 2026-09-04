"""Writes the brief shown under a prompt before the code agent acts.

The brief is Headroom's own interpretation of what a prompt asks for --
the goal, what is not the goal, which files probably matter, and which
skills to run -- shown to Christopher before the code agent starts work.
It never changes the prompt itself.

Claude Code runs this on every `UserPromptSubmit`, so it has to be cheap
and safe: `should_brief` skips prompts too short or too routine to need
one, and `make_brief` puts a hard time budget around the whole thing (both
gathering context and calling the model) so a slow lookup or a stuck
subprocess can never hold up the user's prompt. Anything that goes wrong
-- a timeout, a bad gatherer, a broken model call -- yields `None`, and
the caller (the `headroom code-agent brief` CLI command) prints nothing
and exits 0 in that case.

`make_brief` takes its context gatherer and its model caller as
parameters rather than calling them directly, so tests can swap in fakes
and never touch a real memory database or shell out to `claude`.
"""

from __future__ import annotations

import os
import re
import shutil
import string
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from headroom._subprocess import run

# Set on the child `claude -p` process the default model runner spawns, and
# checked at the top of the `headroom code-agent brief` CLI command. The child
# loads no settings files, so it has no hooks and this should never fire; it
# is a second line of defense against a nested UserPromptSubmit hook calling
# this command again.
RECURSION_GUARD_ENV = "HEADROOM_BRIEF_ACTIVE"

# Env vars that route Claude Code through a proxy; the brief's helper call
# drops them so the call goes straight to the API.
_PROXY_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
)

_MODEL_NAME = "claude-haiku-4-5-20251001"

_MAX_LIKELY_FILES = 8
_MAX_MEMORIES = 5

_MIN_WORDS = 12

_PLAIN_REPLIES = {
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "ok",
    "okay",
    "continue",
    "go",
    "y",
    "n",
}

_SYSTEM_PROMPT = (
    "You write a short brief that appears under a user's prompt before a "
    "coding agent acts on it. You cannot read files; use only the prompt and "
    "the context given to you. Write exactly these lines, in plain language, "
    "with no preamble and no extra commentary:\n"
    "Goal: <one sentence, what the prompt is asking for>\n"
    "Not the goal: <one sentence, something this is not asking for>\n"
    "Likely files: <a short comma-separated list of files, or 'none obvious'>\n"
    "Skills to run: <names from this list only, comma-separated, or 'none': "
    "tdd (any code written or bug fixed); grill-with-docs (a plan, spec or "
    "design is written or reviewed); domain-modeling (terminology or "
    "CONTEXT.md); codebase-design (shaping a module or seam); "
    "improve-codebase-architecture (structural refactor); code-review (before "
    "a branch is done); simplify (after a change lands); pyrefly-autofix (type "
    "checker errors); scaffold-first (new repo, tool with a config file, or "
    "framework choice)>\n"
    "Add a fifth line, Ambiguity: <one sentence>, only if the prompt is "
    "genuinely ambiguous. Otherwise stop after four lines."
)


@dataclass(frozen=True)
class GatheredContext:
    """What the default gatherer collected for one prompt.

    `glossary` holds (term, definition) pairs from the project's
    `CONTEXT.md` whose term appears in the prompt. `memories` holds the
    text of the top headroom memory hits. `likely_files` holds project
    file paths the prompt seems to be about.
    """

    memories: list[str]
    glossary: list[tuple[str, str]]
    likely_files: list[str]


# A gatherer takes the prompt and the working directory and returns what it
# found. A model runner takes the system text, the user text, and a timeout
# in seconds, and returns the model's reply.
GatherFn = Callable[[str, str], GatheredContext]
ModelRunnerFn = Callable[[str, str, float], str]


def should_brief(prompt: str) -> bool:
    """Whether `make_brief` should run for this prompt.

    No brief for slash commands (the prompt starts with `/`), prompts
    under twelve words, and plain replies like "yes" or "ok" (case
    insensitive, trailing punctuation ignored). Everything else gets one.
    """
    stripped = prompt.strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return False

    words = stripped.split()
    if len(words) < _MIN_WORDS:
        return False

    if len(words) == 1:
        bare = words[0].strip(string.punctuation).lower()
        if bare in _PLAIN_REPLIES:
            return False

    return True


def _run_with_budget(build: Callable[[], str], budget_seconds: float) -> str | None:
    """Run `build` on a daemon thread and wait at most `budget_seconds`.

    Returns `None` on timeout or on any exception `build` raises. The
    thread is a daemon so an abandoned, still-running `build` (past the
    budget) never keeps the process alive -- it is simply left to finish
    or die on its own.
    """
    outcome: dict[str, str] = {}

    def _target() -> None:
        try:
            outcome["value"] = build()
        except Exception:
            pass

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=budget_seconds)
    if thread.is_alive():
        return None
    return outcome.get("value")


def make_brief(
    prompt: str,
    *,
    cwd: str,
    gather: GatherFn,
    model_runner: ModelRunnerFn,
    budget_seconds: float = 8.0,
) -> str | None:
    """Return a brief for `prompt`, or `None` if none applies or it failed.

    Runs `should_brief` first -- when it is False, `gather` and
    `model_runner` are never called. Otherwise gathering context and
    calling the model together are capped at `budget_seconds`; going over
    that, or either one raising, yields `None` rather than propagating.
    """
    if not should_brief(prompt):
        return None

    def _build() -> str:
        context = gather(prompt, cwd)
        user_text = _render_user_text(prompt, context)
        return model_runner(_SYSTEM_PROMPT, user_text, budget_seconds)

    return _run_with_budget(_build, budget_seconds)


def _render_user_text(prompt: str, context: GatheredContext) -> str:
    lines = [f"User prompt: {prompt}"]

    if context.glossary:
        lines.append("")
        lines.append("Project terms:")
        for term, definition in context.glossary:
            lines.append(f"- {term}: {definition}")

    if context.memories:
        lines.append("")
        lines.append("Relevant memories:")
        for memory in context.memories:
            lines.append(f"- {memory}")

    if context.likely_files:
        lines.append("")
        lines.append("Files that already look relevant:")
        for path in context.likely_files:
            lines.append(f"- {path}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The default gatherer.
# ---------------------------------------------------------------------------

_TERM_LINE = re.compile(r"^\*\*(?P<term>[^*]+)\*\*:\s*$")


def _parse_glossary(text: str) -> list[tuple[str, str]]:
    """Pull (term, definition) pairs out of a CONTEXT.md-shaped glossary.

    A term is a line that is exactly `**Term**:`. Its definition is every
    non-blank line after it, up to a blank line, an `_Avoid_:` line, a
    heading, or the next term.
    """
    entries: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _TERM_LINE.match(lines[i].strip())
        if match is None:
            i += 1
            continue
        term = match.group("term").strip()
        i += 1
        definition_words: list[str] = []
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith("_Avoid_") or line.startswith("#"):
                break
            if _TERM_LINE.match(line) is not None:
                break
            definition_words.append(line)
            i += 1
        if definition_words:
            entries.append((term, " ".join(definition_words)))
    return entries


def _matching_glossary_terms(prompt: str, root: Path) -> list[tuple[str, str]]:
    context_path = root / "CONTEXT.md"
    try:
        text = context_path.read_text(encoding="utf-8")
    except OSError:
        return []

    prompt_lower = prompt.lower()
    matches: list[tuple[str, str]] = []
    for term, definition in _parse_glossary(text):
        pattern = r"\b" + re.escape(term.lower()) + r"\b"
        if re.search(pattern, prompt_lower):
            matches.append((term, definition))
    return matches


def _search_memories(prompt: str, cwd: str) -> list[str]:
    """The top headroom memory hits for `prompt`, or `[]` quickly if none.

    Checks the database file exists before doing anything else -- a
    project with no memory database yet should not pay for spinning up
    the embedder just to find that out.
    """
    db_path = Path(cwd) / ".headroom" / "memory.db"
    if not db_path.exists():
        return []

    import asyncio

    from headroom.memory.backends.local import LocalBackend, LocalBackendConfig

    async def _search() -> list[str]:
        config = LocalBackendConfig(db_path=str(db_path), embedder_backend="onnx")
        backend = LocalBackend(config)
        try:
            user_id = os.environ.get("USER", os.environ.get("USERNAME", "default"))
            results = await backend.search_memories(
                query=prompt, user_id=user_id, top_k=_MAX_MEMORIES
            )
            return [result.memory.content for result in results]
        finally:
            await backend.close()

    try:
        return asyncio.run(_search())
    except Exception:
        return []


_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_./\-]+|[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,8}")
# An identifier worth matching looks like code, not prose: it has an
# underscore, a digit, an inner capital, or is at least six characters long.
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{2,}")
_CODE_LIKE = re.compile(r"_|\d|[a-z][A-Z]|^.{6,}$")


def _tracked_files(root: Path) -> list[str]:
    """Every file `root` tracks in git, or `[]` when it is not a git repo."""
    git_bin = shutil.which("git")
    if git_bin is None:
        return []
    try:
        result = run(
            [git_bin, "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _likely_files(prompt: str, root: Path) -> list[str]:
    """Tracked files whose path, name, or stem a token in the prompt names."""
    tracked = _tracked_files(root)
    if not tracked:
        return []

    path_tokens = {match.group(0).lower() for match in _PATH_TOKEN.finditer(prompt)}
    word_tokens = {match.group(0).lower() for match in _IDENTIFIER_TOKEN.finditer(prompt)}
    code_tokens = {token for token in word_tokens if _CODE_LIKE.search(token)}
    if not path_tokens and not word_tokens:
        return []

    # Files named outright come first, then files inside a folder the prompt
    # names with a code-like word. A prose word only ever matches a whole file
    # stem, so "work" never matches workflows.
    by_name: list[str] = []
    by_folder: list[str] = []
    for path in tracked:
        stem = Path(path).stem.lower()
        folders = {part.lower() for part in Path(path).parts[:-1]}
        if any(token in path.lower() for token in path_tokens) or stem in word_tokens:
            by_name.append(path)
        elif code_tokens & folders:
            by_folder.append(path)
    return (by_name + by_folder)[:_MAX_LIKELY_FILES]


def gather(prompt: str, cwd: str) -> GatheredContext:
    """The default gatherer: `CONTEXT.md` terms, memory hits, likely files."""
    root = Path(cwd)
    return GatheredContext(
        memories=_search_memories(prompt, cwd),
        glossary=_matching_glossary_terms(prompt, root),
        likely_files=_likely_files(prompt, root),
    )


# ---------------------------------------------------------------------------
# The default model runner.
# ---------------------------------------------------------------------------


def default_model_runner(system: str, user: str, timeout: float) -> str:
    """Run the brief prompt through Haiku via `claude -p` and return the text.

    The child skips every settings file (so no hooks, and no recursion into
    this brief), every MCP server, and every tool, answers in one turn, and
    gets our short system prompt instead of the coding-agent default. It keeps
    the normal login, which the `--bare` flag would drop. `RECURSION_GUARD_ENV` is set on the child's
    environment as a second guard in case that ever changes.
    """
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        raise RuntimeError("'claude' not found in PATH")

    argv = [
        claude_bin,
        "-p",
        "--model",
        _MODEL_NAME,
        "--output-format",
        "text",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--tools",
        "",
        "--max-turns",
        "1",
        "--system-prompt",
        system,
    ]
    env = dict(os.environ)
    env[RECURSION_GUARD_ENV] = "1"
    # Talk to the API directly, not through the headroom proxy: the proxy adds
    # memory tools to every request, which turns this one-shot answer into a
    # tool-calling conversation. Thinking is off for the same reason: speed.
    for key in _PROXY_ENV_KEYS:
        env.pop(key, None)
    env["MAX_THINKING_TOKENS"] = "0"

    result = run(argv, input=user, capture_output=True, text=True, timeout=timeout, env=env)
    stdout: str = result.stdout.strip()
    if result.returncode != 0:
        stderr: str = result.stderr.strip()
        detail = stderr or stdout
        raise RuntimeError(detail or f"exit code {result.returncode}")
    return stdout
