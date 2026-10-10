from types import SimpleNamespace
import pytest
from jusi.application.editor_actions import EditorActionManager, EditorActionError


def setup():
    events = []
    manager = EditorActionManager(lambda _: None, publish_attention=events.append)
    for suffix in ('one', 'two'):
        manager.register(SimpleNamespace(client_id='cli_'+suffix, runtime_id='run_one', notebook_id='nb_one',
            cell_id='cell_'+suffix, capabilities=('attention',)))
        manager.bind('cli_'+suffix, 'editor_'+suffix)
    return manager, events


def request(manager, kind='action_required'):
    return manager.attention('cli_one', dict(action='attention', operation='request', kind=kind, message='Please review'))['attention_id']


def test_attention_acceptance_does_not_require_connected_editor_and_cleanup_is_exact():
    manager, events = setup()
    attention_id = request(manager)
    assert events[-1]['state'] == 'pending'
    manager.connect('editor_one', 'connection_one')
    manager.disconnect('editor_one', 'connection_one')
    assert manager.pending_attention()[0]['attention_id'] == attention_id
    with pytest.raises(EditorActionError): manager.dismiss_attention(attention_id, 'editor_one', 1)
    with pytest.raises(EditorActionError): manager.attention('cli_two', dict(action='attention', operation='clear', attention_id=attention_id))
    manager.attention('cli_one', dict(action='attention', operation='update', attention_id=attention_id, kind='action_required', message='Question changed'))
    assert events[-1]['revision'] == 2
    manager.bind('cli_one', 'editor_new')
    assert manager.pending_attention()[0]['editor_id'] == 'editor_new'
    manager.close_client('cli_two')
    assert manager.pending_attention()
    manager.close_client('cli_one')
    assert not manager.pending_attention() and events[-1]['state'] == 'cleared'


def test_only_current_notice_can_be_dismissed_and_cleared_items_cannot_resurrect():
    manager, events = setup()
    attention_id = request(manager, 'notice')
    with pytest.raises(EditorActionError): manager.dismiss_attention(attention_id, 'editor_two', 1)
    manager.attention('cli_one', dict(action='attention', operation='update', attention_id=attention_id, kind='notice', message='New result'))
    with pytest.raises(EditorActionError): manager.dismiss_attention(attention_id, 'editor_one', 1)
    assert manager.dismiss_attention(attention_id, 'editor_one', 2)['dismissed']
    assert manager.dismiss_attention(attention_id, 'editor_one', 2)['dismissed']
    with pytest.raises(EditorActionError):
        manager.attention('cli_one', dict(action='attention', operation='update', attention_id=attention_id, kind='notice', message='Late update'))
    assert manager.attention('cli_one', dict(action='attention', operation='clear', attention_id=attention_id))['outcome'] == 'accepted'


def test_attention_capability_and_capacity_are_client_scoped():
    manager, _ = setup()
    with pytest.raises(EditorActionError): manager.submit('cli_one', dict(action='copy', text='x', regtype='v'))
    for _ in range(32): request(manager)
    with pytest.raises(EditorActionError, match='capacity'): request(manager)
    manager.attention('cli_two', dict(action='attention', operation='request', kind='notice', message='Other client'))
    manager.close()
    assert not manager.pending_attention()
    with pytest.raises(EditorActionError): request(manager)
