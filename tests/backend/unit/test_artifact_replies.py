from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace
import pytest
from jusi.application.editor_actions import EditorActionManager, EditorActionError


def owner(capabilities=('editor_actions', 'source_context')):
    notices = Queue()
    manager = EditorActionManager(notices.put, timeout=2)
    manager.register(SimpleNamespace(capabilities=capabilities, client_id='client', runtime_id='runtime', notebook_id='notebook', cell_id='cell'))
    manager.connect('editor', 'connection'); manager.bind('client', 'editor')
    return manager, notices


def test_reply_owned_offsets_completion_and_client_close():
    manager, notices = owner()
    with ThreadPoolExecutor() as pool:
        task = pool.submit(manager.submit, 'client', {'action': 'source_snapshot', 'text': '', 'slot': 'source', 'context_id': 'ctx_1'})
        action = notices.get(timeout=2)['action_id']
        try:
            with pytest.raises(EditorActionError): manager.upload_reply(action, 'editor', offset=0, text='part', eof=False)
            manager.fetch(action, 'editor', 0)
            with pytest.raises(EditorActionError): manager.upload_reply(action, 'other', offset=0, text='part', eof=False)
            with pytest.raises(EditorActionError): manager.acknowledge(action, 'editor', 'delivered')
            manager.upload_reply(action, 'editor', offset=0, text='part', eof=False)
            manager.upload_reply(action, 'editor', offset=0, text='part', eof=False)  # exact replay
            with pytest.raises(EditorActionError): manager.upload_reply(action, 'editor', offset=0, text='evil', eof=False)
            with pytest.raises(EditorActionError): manager.upload_reply(action, 'editor', offset=8, text='gap', eof=False)
            manager.upload_reply(action, 'editor', offset=4, text='', eof=True)
            manager.acknowledge(action, 'editor', 'delivered')
            result = task.result(timeout=2)
            assert result['_reply'].read(0)[0] == 'part'
            result['_reply'].close()
        finally: manager.close()


def test_snapshot_capability_and_recipient_replacement():
    manager, _ = owner(('editor_actions',))
    with pytest.raises(EditorActionError, match='capture source'):
        manager.submit('client', {'action': 'source_snapshot', 'text': '', 'slot': 'source', 'context_id': ''})
    manager.close()

    manager, notices = owner()
    with ThreadPoolExecutor() as pool:
        task = pool.submit(manager.submit, 'client', {'action': 'source_snapshot', 'text': '', 'slot': 'source', 'context_id': ''})
        action = notices.get(timeout=2)['action_id']
        manager.fetch(action, 'editor', 0)
        manager.upload_reply(action, 'editor', offset=0, text='partial', eof=False)
        snapshot = manager._actions[action].reply
        manager.bind('client', 'replacement')
        assert task.result(timeout=2)['outcome'] == 'unknown'
        assert snapshot.stream.closed
        with pytest.raises(EditorActionError): manager.upload_reply(action, 'editor', offset=7, text='', eof=True)
    manager.close()


def test_offer_uses_editor_capability_and_bounded_owned_reply():
    manager, notices = owner(('editor_actions',))
    content = {'action': 'offer_document', 'text': '{}', 'slot': 'book',
               'provider': '', 'lifetime': 'independent', 'shared_token': ''}
    with ThreadPoolExecutor() as pool:
        task = pool.submit(manager.submit, 'client', content)
        try:
            action = notices.get(timeout=2)['action_id']
            manager.fetch(action, 'editor', 0)
            with pytest.raises(EditorActionError):
                manager.upload_reply(action, 'other', offset=0, text='{}', eof=True)
            with pytest.raises(EditorActionError):
                manager.acknowledge(action, 'editor', 'delivered')
            reply = '{"transport":"http"}' + ' ' * (65536 - len('{"transport":"http"}'))
            manager.upload_reply(action, 'editor', offset=0, text=reply, eof=False)
            with pytest.raises(EditorActionError):
                manager.upload_reply(action, 'editor', offset=65536, text=' ', eof=True)
            manager.upload_reply(action, 'editor', offset=65536, text='', eof=True)
            manager.acknowledge(action, 'editor', 'delivered')
            result = task.result(timeout=2)
            assert result['outcome'] == 'delivered'
            result['_reply'].close()
        finally:
            manager.close()
