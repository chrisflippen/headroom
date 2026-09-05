"""Send a message to another Claude Code session running on this machine.

Claude Code keeps a registry of its running sessions under
``~/.claude/sessions/<pid>.json``. Each entry names the session and points at a
local Unix socket (``messagingSocketPath``); a message written to that socket
as one JSON line lands in the session's inbox, and an idle session wakes up
and acts on it. Claude Code's own ``SendMessage`` tool does exactly this, but
the Claude desktop app switches that tool off. This module gives the code
agent the same ability through headroom's MCP server.

The wire format is Claude Code's local peer protocol, version 1 (read out of
the Claude Code 2.1.260 binary): one newline-terminated JSON object with
``msgV``, ``msg_id``, ``type: "user"``, ``message: {role, content}`` and
``priority``; no reply is sent back, a clean close means delivered. The body
is wrapped in the same ``<cross-session-message>`` envelope Claude Code uses,
so the recipient sees who sent it and knows where to reply. If a future Claude
Code release changes this format, this module is the one place to update.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headroom._subprocess import pid_alive as _pid_alive
from headroom._subprocess import run as _run

# Claude Code drops any line longer than this, counting an auth line it may
# prepend; refuse before sending, leaving room for that line.
MAX_FRAME_CHARS = 1_048_576
_AUTH_LINE_ALLOWANCE = 64

# How far up the process tree to look for the session that launched this
# server. Claude Code is normally the direct parent of `headroom mcp serve`,
# but a wrapper (the desktop app's `disclaimer` helper) can sit in between.
_MAX_ANCESTOR_HOPS = 4

# After the write, give the kernel a moment to flush before half-closing --
# the same pause Claude Code's own sender takes on macOS.
_FLUSH_PAUSE_SECONDS = 0.15
_CONNECT_TIMEOUT_SECONDS = 5.0

ENVELOPE_TAG = "cross-session-message"


class DeliveryError(Exception):
    """The message could not be handed to the recipient's socket."""


@dataclass(frozen=True)
class Peer:
    """One running Claude Code session, as its registry entry describes it."""

    name: str
    session_id: str
    pid: int
    cwd: str
    socket_path: str
    status: str

    def describe(self) -> str:
        return f"{self.name} (pid {self.pid}, {self.status}, in {self.cwd})"


def default_sessions_dir() -> Path:
    return Path.home() / ".claude" / "sessions"


def _read_entry(path: Path) -> Peer | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return Peer(
            name=str(raw["name"]),
            session_id=str(raw.get("sessionId", "")),
            pid=int(raw["pid"]),
            cwd=str(raw.get("cwd", "")),
            socket_path=str(raw["messagingSocketPath"]),
            status=str(raw.get("status") or "unknown"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_peers(
    sessions_dir: Path,
    *,
    pid_alive: Callable[[int], bool] = _pid_alive,
    self_pid: int | None = None,
) -> list[Peer]:
    """Every other live session in the registry, sorted by name.

    Entries whose process is gone are stale leftovers and are skipped, as is
    the calling session itself.
    """
    peers: list[Peer] = []
    if not sessions_dir.is_dir():
        return peers
    for path in sorted(sessions_dir.glob("*.json")):
        peer = _read_entry(path)
        if peer is None or peer.pid == self_pid or not pid_alive(peer.pid):
            continue
        peers.append(peer)
    peers.sort(key=lambda p: p.name)
    return peers


def _default_parent_of(pid: int) -> int | None:
    result = _run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    text = result.stdout.strip() if result.returncode == 0 else ""
    return int(text) if text.isdigit() else None


def own_session(
    sessions_dir: Path,
    *,
    pid: int,
    parent_of: Callable[[int], int | None] = _default_parent_of,
) -> Peer | None:
    """The registry entry for the session that owns this process tree.

    Starts at ``pid`` and walks up a few parents, so a wrapper process between
    Claude Code and this server does not hide the session. ``None`` when no
    ancestor is a registered session -- the desktop app launches MCP servers
    from the app itself, not from the session, so there the caller has to
    say who it is (the tool's ``from`` argument).
    """
    current: int | None = pid
    for _ in range(_MAX_ANCESTOR_HOPS):
        if current is None or current <= 1:
            return None
        peer = _read_entry(sessions_dir / f"{current}.json")
        if peer is not None:
            return peer
        current = parent_of(current)
    return None


def resolve_peer(to: str, peers: Sequence[Peer]) -> Peer:
    """Find the recipient by name, session id, or pid.

    Raises ``KeyError`` whose message names the sessions that are reachable,
    so the caller can show the model what it can pick from.
    """
    wanted = to.strip()
    for peer in peers:
        if wanted in (peer.name, peer.session_id, str(peer.pid)):
            return peer
    raise KeyError(describe_unknown(wanted, peers))


def describe_unknown(to: str, peers: Sequence[Peer]) -> str:
    if not peers:
        return f"no session named '{to}' is reachable; no other Claude Code sessions are running"
    names = ", ".join(f"'{p.name}'" for p in peers)
    return f"no session named '{to}' is reachable; reachable sessions are {names}"


def _clean_attribute(value: str) -> str:
    # The recipient parses the envelope with a strict pattern; these characters
    # would break it, so they are dropped rather than escaped.
    return "".join(ch for ch in value if ch not in '"<>\n\r')


def build_frame(body: str, *, from_socket: str | None, from_name: str | None) -> str:
    """One newline-terminated JSON line in Claude Code's peer message shape."""
    attributes = ""
    if from_socket:
        attributes += f' from="uds:{_clean_attribute(from_socket)}"'
    if from_name:
        attributes += f' from-name="{_clean_attribute(from_name)}"'
    if attributes:
        content = f"<{ENVELOPE_TAG}{attributes}>\n{body}\n</{ENVELOPE_TAG}>"
    else:
        content = body
    frame = (
        json.dumps(
            {
                "msgV": 1,
                "msg_id": str(uuid.uuid4()),
                "type": "user",
                "message": {"role": "user", "content": content},
                "priority": "next",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    if len(frame) + _AUTH_LINE_ALLOWANCE >= MAX_FRAME_CHARS:
        raise ValueError(
            f"message too large: {len(frame):,} characters; Claude Code accepts fewer "
            f"than {MAX_FRAME_CHARS - _AUTH_LINE_ALLOWANCE:,} per message"
        )
    return frame


def _write_to_socket(socket_path: str, data: bytes) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
        sock.connect(socket_path)
        sock.sendall(data)
        time.sleep(_FLUSH_PAUSE_SECONDS)
        sock.shutdown(socket.SHUT_WR)
        # Claude Code never answers on this socket; draining until it closes
        # is how we know the line was read rather than dropped mid-write.
        try:
            while sock.recv(4096):
                pass
        except OSError:
            pass


def send(
    peer: Peer,
    body: str,
    *,
    from_socket: str | None,
    from_name: str | None,
    write: Callable[[str, bytes], None] = _write_to_socket,
) -> str:
    """Hand ``body`` to ``peer`` and say what happened in one line."""
    frame = build_frame(body, from_socket=from_socket, from_name=from_name)
    try:
        write(peer.socket_path, frame.encode("utf-8"))
    except OSError as exc:
        raise DeliveryError(
            f"could not reach {peer.describe()} at {peer.socket_path}: {exc}. "
            "The session may have exited; run action='list' to see who is reachable."
        ) from exc
    return (
        f"Delivered to {peer.describe()}. If it is idle it will start a turn on this "
        "message now; if it is busy it will read it when the current turn ends."
    )


def _identify_sender(
    arguments: dict[str, Any],
    sessions_dir: Path,
    *,
    self_pid: int | None,
    pid_alive: Callable[[int], bool],
    parent_of: Callable[[int], int | None],
) -> Peer | None:
    """Who is sending: the ``from`` argument if given (a name, id, or pid in
    the registry), otherwise the session that owns this process tree."""
    wanted = str(arguments.get("from") or "").strip()
    if wanted:
        everyone = list_peers(sessions_dir, pid_alive=pid_alive)
        try:
            return resolve_peer(wanted, everyone)
        except KeyError:
            return None
    if self_pid is None:
        return None
    return own_session(sessions_dir, pid=self_pid, parent_of=parent_of)


def send_message(
    arguments: dict[str, Any],
    *,
    sessions_dir: Path | None = None,
    self_pid: int | None = None,
    pid_alive: Callable[[int], bool] = _pid_alive,
    parent_of: Callable[[int], int | None] = _default_parent_of,
    write: Callable[[str, bytes], None] = _write_to_socket,
) -> str:
    """The SendMessage tool: ``action`` 'list' shows who is reachable,
    'send' (the default) delivers ``message`` to ``to``."""
    sessions_dir = sessions_dir if sessions_dir is not None else default_sessions_dir()
    me = _identify_sender(
        arguments, sessions_dir, self_pid=self_pid, pid_alive=pid_alive, parent_of=parent_of
    )
    peers = list_peers(sessions_dir, pid_alive=pid_alive, self_pid=me.pid if me else self_pid)
    action = str(arguments.get("action") or "send")

    if action == "list":
        if not peers:
            return "No other Claude Code sessions are running on this machine."
        return "Reachable sessions:\n" + "\n".join(f"- {p.describe()}" for p in peers)

    to = str(arguments.get("to") or "").strip()
    body = arguments.get("message")
    if not to:
        return "Refused: 'to' is required -- a session name, session id, or pid from action='list'."
    if not isinstance(body, str) or not body.strip():
        return "Refused: 'message' is required and must be non-empty text."

    try:
        peer = resolve_peer(to, peers)
    except KeyError as exc:
        return f"Refused: {exc.args[0]}"

    try:
        report = send(
            peer,
            body,
            from_socket=me.socket_path if me else None,
            from_name=me.name if me else None,
            write=write,
        )
    except (DeliveryError, ValueError) as exc:
        return f"Failed: {exc}"
    if me is None:
        report += (
            " Sent unsigned: this server could not tell which session it belongs to, so the "
            "recipient does not know who sent it -- pass 'from' with your own session name "
            "next time."
        )
    return report
