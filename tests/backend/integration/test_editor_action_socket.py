import os
from pathlib import Path
from queue import Queue
import subprocess
import sys

import pytest
from types import SimpleNamespace

from jusi.application.editor_actions import EditorActionManager
from jusi.infrastructure.editor_action_socket import LocalEditorActionBroker


@pytest.mark.parametrize("text", ["  α\tβ\n\n", "Unicode 0 α\n" * 500000], ids=["small", "six-megabytes"])
def test_file_helper_reads_at_target_and_waits_for_editor_confirmation(tmp_path: Path, text: str):
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=3)
    manager.register(SimpleNamespace(client_id="cli_file", runtime_id="run_file", notebook_id="nb_file", cell_id="cell_file"))
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
    manager.register(SimpleNamespace(client_id="cli_file", runtime_id="run_file", notebook_id="nb_file", cell_id="cell_file"))
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
