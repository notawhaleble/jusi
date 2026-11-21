import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
import socket
import threading

import pytest
from types import SimpleNamespace

from jusi.application.editor_actions import EditorActionManager
from jusi.infrastructure.editor_action_socket import LocalEditorActionBroker


def test_idle_listener_wakes_on_close_and_closes_incomplete_requests(monkeypatch):
    from jusi.infrastructure import editor_action_socket

    waiting = threading.Event()
    select = editor_action_socket.select.select

    def observe_wait(*args):
        waiting.set()
        return select(*args)

    monkeypatch.setattr(editor_action_socket.select, "select", observe_wait)
    endpoint = editor_action_socket._Endpoint(lambda _: pytest.fail("incomplete request was submitted"))
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert waiting.wait(1)
        peer.settimeout(1)
        peer.connect(endpoint.path)
        # The client sends neither a complete frame nor EOF. Cleanup must wake
        # both the idle accept loop and any reader already assigned this peer.
        peer.sendall(b"\x00")
        endpoint.close()
        assert not endpoint.thread.is_alive()
        assert endpoint.socket.fileno() == -1
        assert endpoint.wakeup_reader.fileno() == -1
        assert endpoint.wakeup_writer.fileno() == -1
        assert not Path(endpoint.path).exists()
        try:
            assert peer.recv(1) == b""
        except ConnectionResetError:
            pass
        endpoint.close()
    finally:
        endpoint.close()
        peer.close()


@pytest.mark.parametrize("text", ["  α\tβ\n\n", "Unicode 0 α\n" * 500000], ids=["small", "six-megabytes"])
def test_file_helper_reads_at_target_and_waits_for_editor_confirmation(tmp_path: Path, text: str):
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(capabilities=("editor_actions",), client_id="cli_file", runtime_id="run_file", notebook_id="nb_file", cell_id="cell_file"))
    manager.connect("editor_file", "connection_file")
    manager.bind("cli_file", "editor_file")
    broker = LocalEditorActionBroker()
    env = broker.open("cli_file", lambda content: manager.submit("cli_file", content))
    source = tmp_path / "source.txt"
    source.write_bytes(text.encode())
    process = subprocess.Popen([sys.executable, "-m", "jusi.editor_client", str(source), "--filetype", "text"],
                               env={**os.environ, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        action = notices.get(timeout=3)
        source.unlink()  # Delivery now depends only on captured content.
        assert process.poll() is None
        chunks, offset = [], 0
        while True:
            fetched = manager.fetch(action["action_id"], "editor_file", offset)
            assert len(fetched["content"]["text"].encode()) <= 65536
            chunks.append(fetched["content"]["text"])
            offset = fetched["next_offset"]
            if fetched["eof"]:
                break
        assert "".join(chunks) == text
        assert {**fetched["content"], "text": ""} == {"action": "open", "text": "", "name": "source.txt", "filetype": "text"}
        manager.acknowledge(action["action_id"], "editor_file", "delivered")
        _, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stderr
    finally:
        manager.close()
        broker.close_client("cli_file")
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
    assert not Path(env["JUSI_EDITOR_ACTION_SOCKET"]).exists()


def test_channel_reports_unavailable_editor_and_unblocks_on_client_close():
    from concurrent.futures import ThreadPoolExecutor
    from jusi.editor_client import deliver, EditorDeliveryError

    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(capabilities=("editor_actions",), client_id="cli_file", runtime_id="run_file", notebook_id="nb_file", cell_id="cell_file"))
    broker = LocalEditorActionBroker()
    env = broker.open("cli_file", lambda content: manager.submit("cli_file", content))
    content = {"action": "copy", "text": "snapshot", "regtype": "v"}
    path = env["JUSI_EDITOR_ACTION_SOCKET"]
    try:
        with pytest.raises(EditorDeliveryError, match="unreachable"):
            deliver(content, socket_path=path)
        manager.connect("editor_file", "connection_file")
        manager.bind("cli_file", "editor_file")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(deliver, content, socket_path=path)
            notices.get(timeout=1)
            manager.close_client("cli_file")
            with pytest.raises(EditorDeliveryError, match="cancelled"):
                future.result(timeout=1)
    finally:
        manager.close()
        broker.close()
    assert not Path(path).exists()


@pytest.mark.parametrize("before,after", [("", ""), ("α\n" * 400000, "changed🙂" * 150000)], ids=["empty", "large"])
def test_application_diff_streams_both_snapshots_and_acknowledges_display(monkeypatch, before, after):
    from concurrent.futures import ThreadPoolExecutor
    from jusi.editor_client import show_diff
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(capabilities=("editor_actions",), client_id="cli_diff", runtime_id="run_diff", notebook_id="nb_diff", cell_id="cell_diff"))
    manager.connect("editor_diff", "connection_diff")
    manager.bind("cli_diff", "editor_diff")
    broker = LocalEditorActionBroker()
    env = broker.open("cli_diff", lambda content: manager.submit("cli_diff", content))
    monkeypatch.setenv("JUSI_EDITOR_ACTION_SOCKET", env["JUSI_EDITOR_ACTION_SOCKET"])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(show_diff, before, after, before_name="old.txt", after_name="new.txt", filetype="text")
            action = notices.get(timeout=3)
            assert action["action"] == "show_diff"
            offset, chunks = 0, []
            while True:
                fetched = manager.fetch(action["action_id"], "editor_diff", offset)
                content = fetched["content"]
                assert content["before_bytes"] == len(before.encode())
                assert content["before_name"] == "old.txt" and content["after_name"] == "new.txt"
                chunks.append(content["text"])
                offset = fetched["next_offset"]
                if fetched["eof"]:
                    break
            data = "".join(chunks).encode()
            assert data[:content["before_bytes"]].decode() == before
            assert data[content["before_bytes"]:].decode() == after
            assert not future.done(), "snapshot transfer was mistaken for display"
            manager.acknowledge(action["action_id"], "editor_diff", "delivered")
            assert future.result(timeout=3)["outcome"] == "delivered"
    finally:
        manager.close()
        broker.close()


def test_open_url_helper_preserves_url_through_private_delivery(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from jusi.editor_client import open_url
    from jusi.plugin_api import open_url as worker_open_url
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(capabilities=("editor_actions",), client_id="cli_web", runtime_id="run_web", notebook_id="nb_web", cell_id="cell_web"))
    manager.connect("editor_web", "connection_web")
    manager.bind("cli_web", "editor_web")
    broker = LocalEditorActionBroker()
    env = broker.open("cli_web", lambda content: manager.submit("cli_web", content))
    monkeypatch.setenv("JUSI_EDITOR_ACTION_SOCKET", env["JUSI_EDITOR_ACTION_SOCKET"])
    url = 'https://example.com/report?q=one%20two&fragment=three#four'
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(open_url, url)
            action = notices.get(timeout=3)
            assert action['action'] == 'open_url' and 'url' not in action
            fetched = manager.fetch(action['action_id'], 'editor_web', 0)
            assert fetched['content'] == worker_open_url(url).result
            assert fetched['eof'] and fetched['offset'] == fetched['next_offset'] == 0
            manager.acknowledge(action['action_id'], 'editor_web', 'delivered')
            assert future.result(timeout=3)['outcome'] == 'delivered'
    finally:
        manager.close()
        broker.close()


def test_managed_url_helpers_preserve_slots_and_acknowledge_close(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from jusi.editor_client import open_url, close_url
    from jusi.plugin_api import open_url as worker_open, close_url as worker_close
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(capabilities=("editor_actions",), client_id="cli_slot", runtime_id="run_slot", notebook_id="nb_slot", cell_id="cell_slot"))
    manager.connect("editor_slot", "connection_slot")
    manager.bind("cli_slot", "editor_slot")
    broker = LocalEditorActionBroker()
    env = broker.open("cli_slot", lambda content: manager.submit("cli_slot", content))
    monkeypatch.setenv("JUSI_EDITOR_ACTION_SOCKET", env["JUSI_EDITOR_ACTION_SOCKET"])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for helper, args, expected in [
                (open_url, ("https://example.com/page",), worker_open("https://example.com/page", slot="page").result),
                (close_url, (), worker_close(slot="page").result),
            ]:
                future = pool.submit(helper, *args, slot="page")
                action = notices.get(timeout=3)
                assert "slot" not in action and "url" not in action
                fetched = manager.fetch(action["action_id"], "editor_slot", 0)
                assert fetched["content"] == expected
                assert not future.done()
                manager.acknowledge(action["action_id"], "editor_slot", "delivered")
                assert future.result(timeout=3)["outcome"] == "delivered"
    finally:
        manager.close()
        broker.close()


def test_source_capture_cancel_releases_pending_reply_and_keeps_client_usable(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from jusi.artifacts import encode
    from jusi.editor_client import source_snapshot

    notices = Queue()
    finished = threading.Event()
    manager = EditorActionManager(notices.put, timeout=10)
    manager.register(SimpleNamespace(capabilities=("editor_actions", "source_context"),
        client_id="cli_source", runtime_id="run_source", notebook_id="nb_source", cell_id="cell_source"))
    manager.connect("editor_source", "connection_source")
    manager.bind("cli_source", "editor_source")

    def submit(content):
        try:
            return manager.submit("cli_source", content)
        finally:
            finished.set()

    broker = LocalEditorActionBroker()
    env = broker.open("cli_source", submit)
    monkeypatch.setenv("JUSI_EDITOR_ACTION_SOCKET", env["JUSI_EDITOR_ACTION_SOCKET"])
    cancel = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(source_snapshot, tmp_path / "cancelled", slot="book", cancel=cancel)
            action = notices.get(timeout=2)["action_id"]
            manager.fetch(action, "editor_source", 0)
            manager.upload_reply(action, "editor_source", offset=0, text="partial", eof=False)
            partial = manager._actions[action].reply
            cancel.set()
            with pytest.raises(InterruptedError, match="cancelled"):
                future.result(timeout=2)
            assert finished.wait(2)
            assert partial.stream.closed
            assert manager.pending() == []
            assert not (tmp_path / "cancelled").exists()

            # A later request uses the same client/channel and receives real bytes.
            page = tmp_path / "page.txt"
            page.write_text("immutable page α\n", encoding="utf-8")
            wire = "".join(encode([(page, "text/plain", "page.txt")], {"page": 3}))
            (tmp_path / "received").mkdir()
            future = pool.submit(source_snapshot, tmp_path / "received", slot="book")
            action = notices.get(timeout=2)["action_id"]
            manager.fetch(action, "editor_source", 0)
            manager.upload_reply(action, "editor_source", offset=0, text=wire, eof=True)
            manager.acknowledge(action, "editor_source", "delivered")
            assert future.result(timeout=2)["metadata"] == {"page": 3}
            assert (tmp_path / "received" / "page.txt").read_bytes() == page.read_bytes()
    finally:
        manager.close()
        broker.close()
