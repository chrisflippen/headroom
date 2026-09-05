"""Tests for headroom.code_tools.messaging -- sending a message to another
Claude Code session on this machine over its local socket."""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from headroom.code_tools import messaging


def _write_registry(sessions_dir: Path, pid: int, name: str, **extra: object) -> Path:
    """Write one Claude Code session registry entry the way Claude Code does."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "pid": pid,
        "sessionId": f"session-{pid}",
        "cwd": f"/work/{name}",
        "name": name,
        "kind": "interactive",
        "status": "idle",
        "messagingSocketPath": str(sessions_dir / f"{pid}.sock"),
    }
    entry.update(extra)
    path = sessions_dir / f"{pid}.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def _alive(pids: set[int]) -> Callable[[int], bool]:
    return lambda pid: pid in pids


Listener = tuple[Path, list[bytes]]


# ---------------------------------------------------------------------------
# Reading the registry
# ---------------------------------------------------------------------------


def test_list_peers_reads_live_entries_and_skips_dead_and_self(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha")
    _write_registry(tmp_path, 200, "beta", status="busy")
    _write_registry(tmp_path, 300, "gone")
    _write_registry(tmp_path, 400, "me")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "100.abcdef.key").write_text('{"peerToken": "x"}', encoding="utf-8")

    peers = messaging.list_peers(tmp_path, pid_alive=_alive({100, 200, 400}), self_pid=400)

    assert [p.name for p in peers] == ["alpha", "beta"]
    assert peers[0].pid == 100
    assert peers[0].session_id == "session-100"
    assert peers[0].socket_path == str(tmp_path / "100.sock")
    assert peers[1].status == "busy"


def test_own_session_finds_the_entry_for_this_pid(tmp_path: Path) -> None:
    _write_registry(tmp_path, 400, "me")

    me = messaging.own_session(tmp_path, pid=400, parent_of=lambda pid: None)

    assert me is not None
    assert me.name == "me"
    assert messaging.own_session(tmp_path, pid=999, parent_of=lambda pid: None) is None


def test_own_session_walks_past_a_wrapper_process(tmp_path: Path) -> None:
    """The desktop app puts a helper between the session and the MCP server;
    the session is still found two hops up, and the walk gives up at the
    top of the tree instead of looping."""
    _write_registry(tmp_path, 400, "me")
    parents = {900: 901, 901: 400, 400: 1}

    me = messaging.own_session(tmp_path, pid=900, parent_of=lambda pid: parents.get(pid))

    assert me is not None and me.name == "me"
    assert messaging.own_session(tmp_path, pid=700, parent_of=lambda pid: 1) is None


# ---------------------------------------------------------------------------
# Naming the recipient
# ---------------------------------------------------------------------------


def test_resolve_peer_accepts_name_session_id_or_pid(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha")
    _write_registry(tmp_path, 200, "beta")
    peers = messaging.list_peers(tmp_path, pid_alive=_alive({100, 200}))

    assert messaging.resolve_peer("alpha", peers).pid == 100
    assert messaging.resolve_peer("session-200", peers).pid == 200
    assert messaging.resolve_peer("200", peers).pid == 200


def test_resolve_peer_unknown_lists_known_names(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha")
    _write_registry(tmp_path, 200, "beta")
    peers = messaging.list_peers(tmp_path, pid_alive=_alive({100, 200}))

    with pytest.raises(KeyError) as excinfo:
        messaging.resolve_peer("nope", peers)

    text = str(excinfo.value)
    assert "nope" in text
    assert "alpha" in text and "beta" in text


# ---------------------------------------------------------------------------
# The frame on the wire
# ---------------------------------------------------------------------------


def test_build_frame_is_one_json_line_in_claude_codes_shape() -> None:
    frame = messaging.build_frame("hello there", from_socket="/tmp/cc-socks/1.sock", from_name="me")

    assert frame.endswith("\n") and frame.count("\n") == 1
    payload = json.loads(frame)
    assert payload["msgV"] == 1
    assert payload["type"] == "user"
    assert payload["priority"] == "next"
    assert payload["message"]["role"] == "user"
    assert len(payload["msg_id"]) == 36
    content = payload["message"]["content"]
    assert content.startswith(
        '<cross-session-message from="uds:/tmp/cc-socks/1.sock" from-name="me">\n'
    )
    assert content.endswith("\nhello there\n</cross-session-message>")


def test_build_frame_without_a_known_sender_sends_a_bare_body() -> None:
    frame = messaging.build_frame("hello", from_socket=None, from_name=None)

    assert json.loads(frame)["message"]["content"] == "hello"


def test_build_frame_strips_characters_that_would_break_the_envelope() -> None:
    frame = messaging.build_frame("hi", from_socket="/tmp/x.sock", from_name='we"ird<name>\n')

    content = json.loads(frame)["message"]["content"]
    assert 'from-name="weirdname"' in content


def test_build_frame_refuses_oversized_messages() -> None:
    # Claude Code counts an auth line it may prepend, so a body that leaves
    # fewer than that line's worth of room is already too large.
    with pytest.raises(ValueError, match="too large"):
        messaging.build_frame(
            "x" * (messaging.MAX_FRAME_CHARS - 120), from_socket=None, from_name=None
        )


# ---------------------------------------------------------------------------
# Delivery over a real Unix socket
# ---------------------------------------------------------------------------


@pytest.fixture
def listener() -> Iterator[Listener]:
    """A throwaway Unix-socket server that records every line it receives,
    standing in for a Claude Code session's messaging socket."""

    # Unix socket paths are capped at about 100 characters on macOS, and
    # pytest's tmp_path is longer than that, so the socket lives in /tmp.
    short_dir = Path(tempfile.mkdtemp(prefix="hr-", dir="/tmp"))
    sock_path = short_dir / "peer.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    received: list[bytes] = []

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            chunks = []
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            received.append(b"".join(chunks))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield sock_path, received
    thread.join(timeout=5)
    server.close()
    shutil.rmtree(short_dir, ignore_errors=True)


def test_send_writes_the_frame_to_the_peers_socket(tmp_path: Path, listener: Listener) -> None:
    sock_path, received = listener
    _write_registry(tmp_path, 100, "alpha", messagingSocketPath=str(sock_path))
    peer = messaging.list_peers(tmp_path, pid_alive=_alive({100}))[0]

    report = messaging.send(peer, "resume please", from_socket=None, from_name=None)

    assert "alpha" in report
    payload = json.loads(received[0].decode("utf-8"))
    assert payload["message"]["content"] == "resume please"


def test_send_reports_a_dead_socket_plainly(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha", messagingSocketPath=str(tmp_path / "missing.sock"))
    peer = messaging.list_peers(tmp_path, pid_alive=_alive({100}))[0]

    with pytest.raises(messaging.DeliveryError, match="alpha"):
        messaging.send(peer, "hello", from_socket=None, from_name=None)


# ---------------------------------------------------------------------------
# The tool entry point
# ---------------------------------------------------------------------------


def test_send_message_list_names_reachable_sessions(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha")
    _write_registry(tmp_path, 200, "beta", status="busy")
    _write_registry(tmp_path, 400, "me")

    text = messaging.send_message(
        {"action": "list"}, sessions_dir=tmp_path, self_pid=400, pid_alive=_alive({100, 200, 400})
    )

    assert "alpha" in text and "idle" in text
    assert "beta" in text and "busy" in text
    assert "me" not in text.split("alpha")[0]


def test_send_message_requires_to_and_message(tmp_path: Path) -> None:
    text = messaging.send_message({"to": "alpha"}, sessions_dir=tmp_path, self_pid=1)
    assert "message" in text.lower()

    text = messaging.send_message({"message": "hi"}, sessions_dir=tmp_path, self_pid=1)
    assert "'to'" in text


def test_send_message_delivers_with_the_senders_identity(
    tmp_path: Path, listener: Listener
) -> None:
    sock_path, received = listener
    _write_registry(tmp_path, 100, "alpha", messagingSocketPath=str(sock_path))
    _write_registry(tmp_path, 400, "me", messagingSocketPath=str(tmp_path / "400.sock"))

    text = messaging.send_message(
        {"to": "alpha", "message": "resume please"},
        sessions_dir=tmp_path,
        self_pid=400,
        pid_alive=_alive({100, 400}),
        parent_of=lambda pid: None,
    )

    assert "alpha" in text
    content = json.loads(received[0].decode("utf-8"))["message"]["content"]
    assert f'from="uds:{tmp_path / "400.sock"}" from-name="me"' in content


def test_send_message_signs_with_the_from_argument(tmp_path: Path, listener: Listener) -> None:
    """In the desktop app the MCP server is not a child of the session, so
    the model names itself with 'from'; that session is then left out of the
    reachable list and used to sign the envelope."""
    sock_path, received = listener
    _write_registry(tmp_path, 100, "alpha", messagingSocketPath=str(sock_path))
    _write_registry(tmp_path, 400, "me", messagingSocketPath=str(tmp_path / "400.sock"))

    listing = messaging.send_message(
        {"action": "list", "from": "me"},
        sessions_dir=tmp_path,
        self_pid=1,
        pid_alive=_alive({100, 400}),
        parent_of=lambda pid: None,
    )
    assert "alpha" in listing and "- me" not in listing

    text = messaging.send_message(
        {"to": "alpha", "message": "hi", "from": "me"},
        sessions_dir=tmp_path,
        self_pid=1,
        pid_alive=_alive({100, 400}),
        parent_of=lambda pid: None,
    )
    assert "unsigned" not in text
    content = json.loads(received[0].decode("utf-8"))["message"]["content"]
    assert 'from-name="me"' in content


def test_send_message_says_so_when_it_cannot_sign(tmp_path: Path, listener: Listener) -> None:
    sock_path, received = listener
    _write_registry(tmp_path, 100, "alpha", messagingSocketPath=str(sock_path))

    text = messaging.send_message(
        {"to": "alpha", "message": "hi"},
        sessions_dir=tmp_path,
        self_pid=1,
        pid_alive=_alive({100}),
        parent_of=lambda pid: None,
    )

    assert "Delivered" in text and "unsigned" in text and "'from'" in text
    assert json.loads(received[0].decode("utf-8"))["message"]["content"] == "hi"


def test_send_message_unknown_recipient_lists_known_names(tmp_path: Path) -> None:
    _write_registry(tmp_path, 100, "alpha")

    text = messaging.send_message(
        {"to": "nope", "message": "hi"}, sessions_dir=tmp_path, self_pid=1, pid_alive=_alive({100})
    )

    assert "nope" in text and "alpha" in text
