import json
import hashlib
from pathlib import Path
import pytest
from jusi import editor_client
from jusi.infrastructure.shared_artifacts import DIRECTORY_ENV, TOKEN_ENV


def test_reuse_never_encodes_or_stages_content(tmp_path, monkeypatch):
    source = tmp_path / 'book.pdf'; source.write_bytes(b'book')
    stage = tmp_path / 'staging'; stage.mkdir()
    monkeypatch.setenv(DIRECTORY_ENV, str(stage)); monkeypatch.setenv(TOKEN_ENV, 'a' * 64)
    calls = []
    def exchange(content, chunks, **kw):
        calls.append(content['action'])
        header = json.loads(''.join(chunks))
        assert header['metadata']['document_id'] == hashlib.sha256(b'book').hexdigest()
        kw['reply_sink'].write('{"transport":"reused"}')
        return {'outcome': 'delivered'}
    monkeypatch.setattr(editor_client, '_deliver_chunks', exchange)
    monkeypatch.setattr('jusi.artifacts.encode', lambda *_: pytest.fail('Reused document was encoded'))
    assert editor_client.open_document(source, media_type='application/pdf', slot='book', lifetime='independent')['outcome'] == 'delivered'
    assert calls == ['offer_document'] and not list(stage.iterdir())


def test_shared_snapshot_is_private_independent_and_cleaned_after_delivery_failure(tmp_path, monkeypatch):
    source = tmp_path / 'book.pdf'; source.write_bytes(b'old immutable book')
    stage = tmp_path / 'staging'; stage.mkdir()
    monkeypatch.setenv(DIRECTORY_ENV, str(stage)); monkeypatch.setenv(TOKEN_ENV, 'a' * 64)
    def exchange(content, chunks, **kw):
        header = json.loads(''.join(chunks))
        if content['action'] == 'offer_document':
            kw['reply_sink'].write('{"transport":"shared"}')
            return {'outcome': 'delivered'}
        assert content['action'] == 'open_shared_document'
        snapshot = stage / content['artifact_id'] / header['files'][0]['name']
        assert snapshot.stat().st_ino != source.stat().st_ino, 'Snapshot must not hard-link the mutable source'
        source.write_bytes(b'new original')
        assert snapshot.read_bytes() == b'old immutable book'
        assert snapshot.stat().st_mode & 0o222 == 0
        raise editor_client.EditorDeliveryError('provider rejected document')
    monkeypatch.setattr(editor_client, '_deliver_chunks', exchange)
    with pytest.raises(editor_client.EditorDeliveryError):
        editor_client.open_document(source, media_type='application/pdf', slot='book')
    assert not list(stage.iterdir())


@pytest.mark.parametrize('replacement', [b'BOOK', b'a larger book'])
def test_changed_source_after_offer_is_rejected_and_staging_cleaned(tmp_path, monkeypatch, replacement):
    source = tmp_path / 'book.pdf'; source.write_bytes(b'book')
    stage = tmp_path / 'staging'; stage.mkdir()
    monkeypatch.setenv(DIRECTORY_ENV, str(stage)); monkeypatch.setenv(TOKEN_ENV, 'a' * 64)
    def exchange(content, chunks, **kw):
        assert content['action'] == 'offer_document'
        source.write_bytes(replacement)
        kw['reply_sink'].write('{"transport":"shared"}')
        return {'outcome': 'delivered'}
    monkeypatch.setattr(editor_client, '_deliver_chunks', exchange)
    with pytest.raises(ValueError, match='Artifact (grew|changed)'):
        editor_client.open_document(source, media_type='application/pdf', slot='book')
    assert not list(stage.iterdir())

def test_http_stream_rejects_changed_identity_before_completion(tmp_path, monkeypatch):
    source = tmp_path / 'book.pdf'
    source.write_bytes(b'book')

    def exchange(content, chunks, **kwargs):
        if content['action'] == 'offer_document':
            source.write_bytes(b'BOOK')
            kwargs['reply_sink'].write('{"transport":"http"}')
        else:
            for record in chunks:
                assert 'end' not in json.loads(record), 'Changed snapshot was committed'

    monkeypatch.setattr(editor_client, '_deliver_chunks', exchange)
    with pytest.raises(ValueError, match='changed after identity offer'):
        editor_client.open_document(source, media_type='application/pdf', slot='book')
