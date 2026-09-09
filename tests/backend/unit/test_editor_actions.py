from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace

import pytest

from jusi.application.editor_actions import EditorActionError, EditorActionManager


def setup(timeout=1):
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=timeout)
    manager.register(SimpleNamespace(client_id="cli_one", runtime_id="run_one", notebook_id="nb_one", cell_id="cell_one"))
    manager.connect("editor_one", "connection_one")
    manager.bind("cli_one", "editor_one")
    return manager, notices


CONTENT = {"action": "copy", "text": "  α\tβ\n", "regtype": "v"}


def test_exact_recipient_ack_replay_and_reconnect():
    manager, notices = setup()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.submit, "cli_one", CONTENT)
        action = notices.get(timeout=1)
        assert "text" not in str(action) and manager.pending() == [action]
        with pytest.raises(EditorActionError):
            manager.fetch(action["action_id"], "editor_other")
        with pytest.raises(EditorActionError):
            manager.acknowledge(action["action_id"], "editor_other", "delivered")
        manager.disconnect("editor_one", "connection_one")
        with pytest.raises(EditorActionError):
            manager.submit("cli_one", CONTENT)
        manager.connect("editor_one", "connection_new")
        # A late close from the old SSE connection cannot disconnect its successor.
        manager.disconnect("editor_one", "connection_one")
        fetched = manager.fetch(action["action_id"], "editor_one")
        assert fetched["content"] == CONTENT and not future.done()
        ack = manager.acknowledge(action["action_id"], "editor_one", "delivered")
        assert future.result(timeout=1) == ack
        assert manager.acknowledge(action["action_id"], "editor_one", "delivered") == ack
        assert manager.pending() == []
        with pytest.raises(EditorActionError):
            manager.fetch(action["action_id"], "editor_one")
    manager.close()


@pytest.mark.parametrize("fetched,outcome", [(False, "cancelled"), (True, "unknown")])
def test_close_releases_waiter_without_claiming_delivery(fetched, outcome):
    manager, notices = setup()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.submit, "cli_one", CONTENT)
        action = notices.get(timeout=1)
        if fetched:
            manager.fetch(action["action_id"], "editor_one")
        manager.close_client("cli_one")
        assert future.result(timeout=1)["outcome"] == outcome
        with pytest.raises(EditorActionError):
            manager.acknowledge(action["action_id"], "editor_one", "delivered")
        assert manager.pending() == []


def test_replacement_editor_does_not_inherit_an_action():
    manager, notices = setup()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.submit, "cli_one", CONTENT)
        action = notices.get(timeout=1)
        manager.connect("editor_new", "connection_new")
        manager.bind("cli_one", "editor_new")
        assert future.result(timeout=1)["outcome"] == "cancelled"
        with pytest.raises(EditorActionError):
            manager.fetch(action["action_id"], "editor_new")
        next_action = pool.submit(manager.submit, "cli_one", CONTENT)
        metadata = notices.get(timeout=1)
        assert metadata["editor_id"] == "editor_new"
        manager.acknowledge(metadata["action_id"], "editor_new", "failed")
        assert next_action.result(timeout=1)["outcome"] == "failed"


def test_delivery_expiry_is_unknown_after_fetch_and_failure_before_fetch():
    for fetch, outcome in [(False, "failed"), (True, "unknown")]:
        manager, notices = setup(timeout=.1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(manager.submit, "cli_one", CONTENT)
            action = notices.get(timeout=1)
            if fetch:
                manager.fetch(action["action_id"], "editor_one")
            result = future.result(timeout=1)
            assert result["outcome"] == outcome and result["reason"] == "timeout"
        manager.close()


def test_pending_capacity_is_bounded_and_close_releases_all_waiters():
    manager, notices = setup(timeout=5)
    with ThreadPoolExecutor(max_workers=33) as pool:
        futures = []
        try:
            for _ in range(32):
                futures.append(pool.submit(manager.submit, "cli_one", CONTENT))
                notices.get(timeout=1)
            with pytest.raises(EditorActionError, match="capacity"):
                manager.submit("cli_one", CONTENT)
            assert len(manager.pending()) == 32
        finally:
            manager.close()
        assert all(future.result(timeout=1)["outcome"] == "cancelled" for future in futures)


def test_delivery_ack_requires_fetch_and_a_definite_outcome():
    manager, notices = setup()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.submit, "cli_one", CONTENT)
        action = notices.get(timeout=1)
        try:
            with pytest.raises(EditorActionError, match="not fetched"):
                manager.acknowledge(action["action_id"], "editor_one", "delivered")
            with pytest.raises(EditorActionError, match="Invalid"):
                manager.acknowledge(action["action_id"], "editor_one", "unknown")
        finally:
            manager.close()
        assert future.result(timeout=1)["outcome"] == "cancelled"
