from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from jusi import editor_client
from jusi.application.editor_actions import EditorActionManager
from jusi.infrastructure.editor_action_socket import LocalEditorActionBroker


def test_attention_helpers_use_private_client_channel_without_editor_or_human_wait(monkeypatch):
    events = []
    manager = EditorActionManager(lambda _: None, publish_attention=events.append)
    manager.register(SimpleNamespace(client_id='cli_attention', runtime_id='run_attention',
        notebook_id='nb_attention', cell_id='cell_attention', capabilities=('attention',)))
    broker = LocalEditorActionBroker()
    env = broker.open('cli_attention', lambda content: manager.attention('cli_attention', content))
    monkeypatch.setenv('JUSI_EDITOR_ACTION_SOCKET', env['JUSI_EDITOR_ACTION_SOCKET'])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            attention_id = pool.submit(editor_client.request_attention, kind='action_required', message='Permission needed').result(timeout=2)
            assert events[-1]['attention_id'] == attention_id and events[-1]['editor_id'] == ''
            manager.bind('cli_attention', 'editor_one')
            pool.submit(editor_client.update_attention, attention_id, kind='action_required', message='Question changed').result(timeout=2)
            assert events[-1]['revision'] == 3
            pool.submit(editor_client.clear_attention, attention_id).result(timeout=2)
            assert events[-1]['state'] == 'cleared' and not manager.pending_attention()
            with pytest.raises(editor_client.EditorDeliveryError, match='rejected'):
                pool.submit(editor_client.update_attention, attention_id, kind='notice', message='Late').result(timeout=2)
    finally:
        broker.close()
        manager.close()
