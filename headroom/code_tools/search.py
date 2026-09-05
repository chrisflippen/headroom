"""Search: the code agent's file-reading and code-finding tool.

The code agent reaches files only through Search — never the built-in Read,
Grep, or Glob. Five actions live on this one entry point:

- ``read``: a smarter Read. Every read's header carries a stamp — a short
  hash of the file's bytes. Pass that stamp back on a later read of the same
  file and, if it still matches, Search returns a one-line "unchanged"
  marker instead of the text, so the model never pays tokens to see the
  same bytes twice. No cache is kept anywhere: the stamp only proves what
  the caller already holds, so a helper agent or a session that just went
  through a context compaction — neither of which has the stamp — always
  gets the full text.
- ``find``: a smarter Glob. Lists files matching a pattern, honoring
  .gitignore.
- ``grep``: a smarter Grep. Runs ripgrep when it's available, else a plain
  Python fallback, and groups matches by file.
- ``symbols``: an outline of a file's classes, functions, and type
  definitions, so the model can see a file's shape before reading it whole.
- ``importers``: finds the files that import a given file, so the model can
  check its callers before renaming or changing its public surface.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headroom.proxy.helpers import safe_decode_for_logging
from headroom.transforms.code_compressor import (
    _LANG_CONFIGS,
    CodeLanguage,
    LangConfig,
    _get_parser,
    detect_language,
)

_STAMP_LENGTH = 12


def file_stamp(content: str) -> str:
    """Return the stamp for a file's content: the first 12 hex characters of
    the sha256 hash of its bytes. Two reads of the same bytes get the same
    stamp; any change to the bytes changes it. Shared with ``edit.py`` so a
    write reports the same stamp a read of the result would."""

    return hashlib.sha256(content.encode()).hexdigest()[:_STAMP_LENGTH]


class PathOutsideRootError(ValueError):
    """Raised when a request path resolves to somewhere outside the root."""


def resolve_path(raw_path: str, root: Path) -> Path:
    """Resolve a request path against ``root``.

    A relative path resolves against ``root``. An absolute path is allowed,
    but it must still land inside ``root``'s tree — anything else is
    refused, so Search can't be used to read files outside the project.
    """

    candidate = Path(raw_path).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PathOutsideRootError(f"path outside root: {raw_path}") from None
    return resolved


def is_unchanged_marker(text: str) -> bool:
    """True when ``text`` is the unchanged-file marker the ``read`` action emits."""

    return text.startswith("<file ") and 'status="unchanged"' in text


def _numbered_lines(content: str) -> str:
    return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(content.split("\n"), 1))


def _line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _read_file_text(path: Path) -> str:
    return safe_decode_for_logging(path.read_bytes())


def _read_range(content: str, rel: str, start: Any, end: Any, stamp: str) -> str:
    lines = content.split("\n")
    total = len(lines)
    try:
        start_line = int(start) if start is not None else 1
        end_line = int(end) if end is not None else total
    except (TypeError, ValueError):
        return "error: start and end must be integers"

    start_line = max(1, start_line)
    end_line = min(total, end_line)
    if start_line > end_line:
        return f"error: empty range: start={start} end={end}"

    selected = lines[start_line - 1 : end_line]
    numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(selected, start_line))
    header = f"{rel}: lines {start_line}-{end_line} of {total} stamp={stamp}"
    return f"{header}\n{numbered}"


def _handle_read(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    try:
        path = resolve_path(raw_path, root)
    except PathOutsideRootError as exc:
        return f"error: {exc}"

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: path is a directory: {raw_path}"

    try:
        content = _read_file_text(path)
    except OSError as exc:
        return f"error: cannot read file: {exc}"

    rel = path.relative_to(root.resolve()).as_posix()
    stamp = file_stamp(content)

    start = request.get("start")
    end = request.get("end")
    if start is not None or end is not None:
        return _read_range(content, rel, start, end, stamp)

    line_count = _line_count(content)
    provided_stamp = request.get("stamp")
    if isinstance(provided_stamp, str) and provided_stamp == stamp:
        return f'<file path="{rel}" status="unchanged" lines="{line_count}" stamp="{stamp}"/>'

    header = f"file: {rel} lines={line_count} stamp={stamp}"
    return f"{header}\n{_numbered_lines(content)}"


# =============================================================================
# find: list files matching a glob, honoring .gitignore
# =============================================================================

_SKIP_DIR_NAMES = {".git", "node_modules", ".venv", "__pycache__"}
_DEFAULT_FIND_LIMIT = 200


def _is_git_repo(root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_tracked_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def _walk_all_files(root: Path) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            full = Path(dirpath) / name
            found.append(full.relative_to(root).as_posix())
    return found


def _list_repo_files(root: Path) -> list[str]:
    """List every file under ``root``, honoring .gitignore when possible.

    Uses ``git ls-files`` when ``root`` is a git repo, since that already
    knows every ignore rule. Otherwise walks the tree by hand, skipping the
    usual noisy directories.
    """

    if _is_git_repo(root):
        return _git_tracked_files(root)
    return _walk_all_files(root)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Turn a glob like ``src/**/*.py`` into a regex over a posix-style path.

    ``**`` stands for zero or more whole path segments. A single ``*`` or
    ``?`` stays inside one segment, the way shell globs work.
    """

    segments = pattern.split("/")
    parts: list[str] = []
    for i, segment in enumerate(segments):
        if segment == "**":
            parts.append(".*" if i == len(segments) - 1 else "(?:.*/)?")
            continue
        piece: list[str] = []
        for ch in segment:
            if ch == "*":
                piece.append("[^/]*")
            elif ch == "?":
                piece.append("[^/]")
            else:
                piece.append(re.escape(ch))
        parts.append("".join(piece))

    body: list[str] = []
    for i, part in enumerate(parts):
        if i > 0 and not parts[i - 1].endswith("/)?"):
            body.append("/")
        body.append(part)
    return re.compile("^" + "".join(body) + "$")


def _parse_int_arg(value: Any, name: str, default: int) -> tuple[int, str]:
    """Parse an optional integer argument. Returns ``(parsed, "")`` or ``(0, error)``."""

    if value is None:
        return default, ""
    try:
        return int(value), ""
    except (TypeError, ValueError):
        return 0, f"error: {name} must be an integer"


def _cap_lines(lines: list[str], limit: int, empty_message: str) -> str:
    if not lines:
        return empty_message
    shown = list(lines[:limit])
    if len(lines) > limit:
        shown.append(f"… {len(lines) - limit} more")
    return "\n".join(shown)


def _handle_find(request: dict[str, Any], root: Path) -> str:
    pattern = str(request.get("pattern") or "**/*")
    limit, err = _parse_int_arg(request.get("limit"), "limit", _DEFAULT_FIND_LIMIT)
    if err:
        return err
    if limit <= 0:
        return "error: limit must be a positive integer"

    regex = _glob_to_regex(pattern)
    matched = sorted(f for f in _list_repo_files(root) if regex.match(f))
    return _cap_lines(matched, limit, "no files found")


_ACTIONS: dict[str, Callable[[dict[str, Any], Path], str]] = {
    "read": _handle_read,
    "find": _handle_find,
}


# =============================================================================
# grep: regex search over file contents, grouped by file
# =============================================================================

_DEFAULT_GREP_LIMIT = 100
_GREP_LINE_MARKER = re.compile(r"[:-](\d+)[:-]")


def _strip_dot_slash(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def _parse_grep_output_fallback(content: str) -> list[tuple[str, int, str]]:
    """Parse ``file:line:text`` and ``file-line-text`` (context) lines.

    Used when the Rust helper isn't built. Scans for the first ``:N:`` or
    ``-N-`` marker on a line to split the file path from the matched text,
    the same shape ripgrep and ``grep -n`` produce.
    """

    results: list[tuple[str, int, str]] = []
    for raw_line in content.splitlines():
        if raw_line == "--":
            continue
        match = _GREP_LINE_MARKER.search(raw_line)
        if not match:
            continue
        file_path = raw_line[: match.start()]
        line_no = int(match.group(1))
        text = raw_line[match.end() :]
        results.append((file_path, line_no, text))
    return results


def _parse_grep_output(content: str) -> list[tuple[str, int, str]]:
    try:
        from headroom._core import parse_search_lines
    except ImportError:
        parsed = _parse_grep_output_fallback(content)
    else:
        parsed = [(str(f), int(n), str(t)) for f, n, t in parse_search_lines(content)]
    return [(_strip_dot_slash(f), n, t) for f, n, t in parsed]


def _grep_with_rg(
    root: Path, pattern: str, search_target: str, glob: str | None, context: int
) -> tuple[list[tuple[str, int, str]], str | None]:
    cmd = [
        "rg",
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--color",
        "never",
        "--sort",
        "path",
    ]
    if glob:
        cmd += ["-g", glob]
    if context:
        cmd += ["-C", str(context)]
    cmd += ["-e", pattern, search_target]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30)
    except OSError as exc:
        return [], f"error: could not run rg: {exc}"
    if proc.returncode == 1:
        return [], None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "ripgrep failed"
        return [], f"error: {detail}"
    return _parse_grep_output(proc.stdout), None


def _grep_with_python(
    root: Path, pattern: str, search_target: str, glob: str | None, context: int
) -> tuple[list[tuple[str, int, str]], str | None]:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return [], f"error: invalid pattern: {exc}"

    prefix = "" if search_target == "." else search_target.rstrip("/") + "/"
    candidates = [f for f in _list_repo_files(root) if f.startswith(prefix)]
    if prefix and not candidates and (root / search_target).is_file():
        candidates = [search_target]
    if glob:
        glob_pattern = glob if "/" in glob else f"**/{glob}"
        glob_regex = _glob_to_regex(glob_pattern)
        candidates = [f for f in candidates if glob_regex.match(f)]

    matches: list[tuple[str, int, str]] = []
    for rel in sorted(candidates):
        try:
            lines = (root / rel).read_text(errors="replace").splitlines()
        except OSError:
            continue
        hits = [i for i, line in enumerate(lines) if regex.search(line)]
        if not hits:
            continue
        keep: set[int] = set()
        for i in hits:
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
        for i in sorted(keep):
            matches.append((rel, i + 1, lines[i]))
    return matches, None


def _grep_matches(
    root: Path, pattern: str, search_target: str, glob: str | None, context: int
) -> tuple[list[tuple[str, int, str]], str | None]:
    if shutil.which("rg"):
        return _grep_with_rg(root, pattern, search_target, glob, context)
    return _grep_with_python(root, pattern, search_target, glob, context)


def _render_grep_matches(matches: list[tuple[str, int, str]], limit: int) -> str:
    if not matches:
        return "no matches"
    total = len(matches)
    shown = matches[:limit]
    lines: list[str] = []
    current_file: str | None = None
    for file_path, line_no, text in shown:
        if file_path != current_file:
            lines.append(f"{file_path}:")
            current_file = file_path
        lines.append(f"  {line_no}: {text}")
    if total > limit:
        lines.append(f"… {total - limit} more")
    return "\n".join(lines)


def _handle_grep(request: dict[str, Any], root: Path) -> str:
    pattern = request.get("pattern")
    if not pattern:
        return "error: pattern is required"
    pattern = str(pattern)

    raw_path = request.get("path")
    search_target = "."
    if raw_path:
        try:
            resolved = resolve_path(str(raw_path), root)
        except PathOutsideRootError as exc:
            return f"error: {exc}"
        if not resolved.exists():
            return f"error: path not found: {raw_path}"
        search_target = resolved.relative_to(root.resolve()).as_posix() or "."

    glob = request.get("glob")
    glob = str(glob) if glob else None

    context, err = _parse_int_arg(request.get("context"), "context", 0)
    if err:
        return err
    if context < 0:
        return "error: context must be zero or greater"

    limit, err = _parse_int_arg(request.get("limit"), "limit", _DEFAULT_GREP_LIMIT)
    if err:
        return err

    matches, error = _grep_matches(root, pattern, search_target, glob, context)
    if error is not None:
        return error
    return _render_grep_matches(matches, limit)


_ACTIONS["grep"] = _handle_grep


# =============================================================================
# symbols: outline of a file's classes, functions, and type definitions
# =============================================================================

_EXTENSION_LANGUAGES: dict[str, CodeLanguage] = {
    ".py": CodeLanguage.PYTHON,
    ".js": CodeLanguage.JAVASCRIPT,
    ".jsx": CodeLanguage.JAVASCRIPT,
    ".mjs": CodeLanguage.JAVASCRIPT,
    ".cjs": CodeLanguage.JAVASCRIPT,
    ".ts": CodeLanguage.TYPESCRIPT,
    ".tsx": CodeLanguage.TYPESCRIPT,
    ".go": CodeLanguage.GO,
    ".rs": CodeLanguage.RUST,
    ".java": CodeLanguage.JAVA,
    ".c": CodeLanguage.C,
    ".h": CodeLanguage.C,
    ".cpp": CodeLanguage.CPP,
    ".cc": CodeLanguage.CPP,
    ".cxx": CodeLanguage.CPP,
    ".hpp": CodeLanguage.CPP,
    ".cs": CodeLanguage.CSHARP,
    ".php": CodeLanguage.PHP,
    ".pl": CodeLanguage.PERL,
}

# Plain regex outline for when tree-sitter can't parse the file. Matches the
# start of a definition line for the handful of keywords the brief calls out.
_REGEX_OUTLINE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:def\s+(\w+)|class\s+(\w+)|function\s+(\w+)|interface\s+(\w+)|type\s+(\w+))"
)


@dataclass
class _Symbol:
    """One line of a symbols outline: a label, its source line, and nesting depth."""

    label: str
    line: int
    depth: int


def _language_for_path(path: Path, content: str) -> CodeLanguage:
    """Work out a file's language from its extension, or its content as a backup."""

    lang = _EXTENSION_LANGUAGES.get(path.suffix.lower())
    if lang is not None:
        return lang
    detected, _confidence = detect_language(content)
    return detected


def _node_name(node: Any, skip_types: frozenset[str]) -> str | None:
    """Pull a symbol's name out of a tree-sitter node.

    Most grammars put the name in a "name" field, so try that first. A few
    (Go's type declarations, Rust's impl blocks) don't, so fall back to a
    breadth-first walk over the children for the first identifier-shaped
    leaf, skipping bodies so we don't grab a name from inside the block.
    """

    name_field = node.child_by_field_name("name")
    if name_field is not None:
        return str(name_field.text.decode("utf-8"))

    queue = list(node.children)
    while queue:
        child = queue.pop(0)
        if child.type in skip_types:
            continue
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return str(child.text.decode("utf-8"))
        queue.extend(child.children)
    return None


def _walk_symbols(node: Any, config: LangConfig, depth: int, out: list[_Symbol]) -> None:
    """Walk a tree-sitter tree, collecting classes/functions/types in source order.

    Methods inside a class are recorded one level deeper than the class
    itself. Function bodies are never opened up, so nested helper functions
    don't show up (the outline only goes one level deep).
    """

    skip_types = config.body_node_types | (config.class_body_node_types or frozenset())

    for child in node.children:
        node_type = child.type

        if config.decorator_node and node_type == config.decorator_node:
            _walk_symbols(child, config, depth, out)
            continue

        if node_type in config.function_nodes:
            name = _node_name(child, skip_types)
            if name:
                out.append(_Symbol(f"def {name}", child.start_point[0] + 1, depth))
            continue

        if node_type in config.class_nodes:
            name = _node_name(child, skip_types)
            if name:
                out.append(_Symbol(f"class {name}", child.start_point[0] + 1, depth))
            body_types = config.class_body_node_types or config.body_node_types
            for grandchild in child.children:
                if grandchild.type in body_types:
                    _walk_symbols(grandchild, config, depth + 1, out)
            continue

        if node_type in config.type_nodes:
            name = _node_name(child, skip_types)
            if name:
                out.append(_Symbol(f"type {name}", child.start_point[0] + 1, depth))
            continue

        if node_type not in skip_types:
            _walk_symbols(child, config, depth, out)


def _render_symbols(rel: str, symbols: list[_Symbol]) -> str:
    if not symbols:
        return f"{rel}:\nno symbols found"
    lines = [f"{s.line:>4}  {'  ' * s.depth}{s.label}" for s in symbols]
    return f"{rel}:\n" + "\n".join(lines)


def _regex_outline_symbols(content: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for i, line in enumerate(content.splitlines()):
        match = _REGEX_OUTLINE_PATTERN.match(line)
        if not match:
            continue
        if match.group(1):
            label = f"def {match.group(1)}"
        elif match.group(2):
            label = f"class {match.group(2)}"
        elif match.group(3):
            label = f"def {match.group(3)}"
        elif match.group(4):
            label = f"type {match.group(4)}"
        elif match.group(5):
            label = f"type {match.group(5)}"
        else:
            continue
        found.append((i + 1, label))
    return found


def _render_regex_outline(rel: str, content: str) -> str:
    found = _regex_outline_symbols(content)
    if not found:
        return f"{rel}:\nno symbols found"
    lines = [f"{line:>4}  {label}" for line, label in found]
    return f"{rel}:\n" + "\n".join(lines)


def _handle_symbols(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    try:
        resolved = resolve_path(str(raw_path), root)
    except PathOutsideRootError as exc:
        return f"error: {exc}"
    if not resolved.exists():
        return f"error: file not found: {raw_path}"
    if resolved.is_dir():
        return f"error: {raw_path} is a directory, not a file"

    try:
        content = resolved.read_text(errors="replace")
    except OSError as exc:
        return f"error: could not read file: {exc}"

    rel = resolved.relative_to(root.resolve()).as_posix()
    language = _language_for_path(resolved, content)
    if language is CodeLanguage.UNKNOWN:
        return (
            f"{_render_regex_outline(rel, content)}\n"
            "(no tree-sitter grammar for this file type, used a regex outline)"
        )

    config = _LANG_CONFIGS.get(language)
    if config is None:
        return (
            f"{_render_regex_outline(rel, content)}\n"
            "(no tree-sitter grammar for this file type, used a regex outline)"
        )

    try:
        parser = _get_parser(language.value)
    except (ImportError, ValueError):
        return f"{_render_regex_outline(rel, content)}\n(tree-sitter unavailable, used a regex outline)"

    tree = parser.parse(bytes(content, "utf-8"))
    found: list[_Symbol] = []
    _walk_symbols(tree.root_node, config, 0, found)
    return _render_symbols(rel, found)


_ACTIONS["symbols"] = _handle_symbols


# =============================================================================
# importers: find files that import a given module
# =============================================================================


def _module_forms(rel_path: str) -> tuple[str, str]:
    """Work out a plain stem and a dotted path for a module from its repo path.

    ``src/a.py`` becomes stem ``a`` and dotted path ``src.a`` — the shape
    Python's ``import src.a`` / ``from src.a import ...`` use. A file
    straight under the root has the same stem and dotted path.
    """

    name = Path(rel_path).name
    without_ext = rel_path.rsplit(".", 1)[0] if "." in name else rel_path
    stem = Path(without_ext).name
    dotted = without_ext.replace("/", ".")
    return stem, dotted


def _importers_pattern(stem: str, dotted: str) -> str:
    """Build one regex that matches an import-style statement naming this module.

    Only ``\\b`` word-boundary assertions are used (no lookaround, no
    backreferences), since this pattern has to work with both ripgrep's
    regex engine and Python's ``re`` module, and ripgrep's default engine
    supports neither of those.
    """

    names = sorted({re.escape(stem), re.escape(dotted)})
    alternation = "|".join(names)
    return rf"\b(?:import|from|require|use)\b[^\n]{{0,120}}?\b(?:{alternation})\b"


def _handle_importers(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    try:
        resolved = resolve_path(str(raw_path), root)
    except PathOutsideRootError as exc:
        return f"error: {exc}"
    if not resolved.exists():
        return f"error: file not found: {raw_path}"

    target_rel = resolved.relative_to(root.resolve()).as_posix()
    stem, dotted = _module_forms(target_rel)
    pattern = _importers_pattern(stem, dotted)

    matches, error = _grep_matches(root, pattern, ".", None, 0)
    if error is not None:
        return error
    matches = [m for m in matches if m[0] != target_rel]
    return _render_grep_matches(matches, _DEFAULT_GREP_LIMIT)


_ACTIONS["importers"] = _handle_importers


def search(request: dict[str, Any], root: Path) -> str:
    """Run one Search action and return the result as plain text.

    ``request`` is the tool call's arguments. ``root`` is the directory a
    relative ``path`` resolves against. Every failure — a bad action, a
    missing file, a path outside root — comes back as one plain text line,
    never a traceback, so a bad request can't crash the agent's turn.
    """

    action = request.get("action")
    handler = _ACTIONS.get(str(action))
    if handler is None:
        return f"error: unknown action: {action!r}"
    return handler(request, root)
