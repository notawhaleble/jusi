import json
from pathlib import Path
import pytest
from jusi.artifacts import encode, decode


def test_binary_roundtrip_and_truncated_stream_cleanup(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(bytes(range(256)) * 500)
    wire = tmp_path / 'wire'; wire.write_text(''.join(encode([(source, 'image/png', 'page.png')], {'page': 4})))
    destination = tmp_path / 'out'; destination.mkdir()
    result = decode(wire, destination)
    assert result['metadata']['page'] == 4
    assert (destination / 'page.png').read_bytes() == source.read_bytes()
    (destination / 'page.png').unlink()
    wire.write_text(wire.read_text().rsplit('\n', 2)[0] + '\n')
    with pytest.raises(ValueError): decode(wire, destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize('mutation', ['path', 'checksum', 'offset', 'size', 'duplicate', 'boolean-version', 'boolean-offset', 'numeric-end'])
def test_invalid_artifacts_never_publish_partial_files(tmp_path, mutation):
    source = tmp_path / 'source'; source.write_bytes(b'abc\0def')
    records = [json.loads(line) for line in encode([(source, 'application/octet-stream', 'file.bin')])]
    if mutation == 'path': records[0]['files'][0]['name'] = '../escaped'
    if mutation == 'checksum': records[1]['data'] = 'YXNk'
    if mutation == 'offset': records[1]['offset'] = 10
    if mutation == 'size': records[0]['files'][0]['size'] = 1
    if mutation == 'duplicate': records.insert(2, records[1])
    if mutation == 'boolean-version': records[0]['version'] = True
    if mutation == 'boolean-offset': records[1]['offset'] = False
    if mutation == 'numeric-end': records[-1]['end'] = 1
    wire = tmp_path / 'wire'; wire.write_text(''.join(json.dumps(r) + '\n' for r in records))
    destination = tmp_path / 'out'; destination.mkdir()
    with pytest.raises(ValueError): decode(wire, destination)
    assert not list(destination.iterdir()) and not (tmp_path / 'escaped').exists()


def test_unicode_metadata_stays_within_wire_record_limit(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'page')
    metadata = {'description': '\U0001f642' * 4000}
    records = list(encode([(source, 'text/plain', 'page.txt')], metadata))
    assert max(len(record.encode('utf-8')) for record in records) <= 65536
    wire = tmp_path / 'wire'; wire.write_text(''.join(records), encoding='utf-8')
    destination = tmp_path / 'out'; destination.mkdir()
    assert decode(wire, destination)['metadata'] == metadata


@pytest.mark.parametrize('name', ['', '.', '..', '../book.pdf', 'a/b.pdf', 'a\\b.pdf', 'C:book.pdf', 'bad\nbook.pdf', 'bad\x00.pdf', 'bad\x7f.pdf', 'я' * 128])
def test_filename_rejects_paths_controls_and_byte_overflow(name):
    from jusi.artifacts import manifest
    with pytest.raises(ValueError):
        manifest({'version': 1, 'metadata': {}, 'files': [{'name': name, 'media_type': 'application/pdf', 'size': 0}]})
