from pathlib import Path
import stat
import pytest
from jusi.infrastructure.shared_artifacts import SharedArtifacts, ROOT_ENV, DIRECTORY_ENV, TOKEN_ENV


def test_negotiation_is_opt_in_and_requires_private_real_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(ROOT_ENV, raising=False)
    assert SharedArtifacts.negotiate() is None
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / 'absent'))
    assert SharedArtifacts.negotiate() is None
    root = tmp_path / 'staging'; root.mkdir(mode=0o700)
    (root / 'challenge').write_text('a' * 64)
    alias = tmp_path / 'alias'; alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv(ROOT_ENV, str(alias))
    assert SharedArtifacts.negotiate() is None
    monkeypatch.setenv(ROOT_ENV, str(root))
    root.chmod(0o755)
    assert SharedArtifacts.negotiate() is None
    root.chmod(0o700)
    store = SharedArtifacts.negotiate()
    assert store.token == 'a' * 64 and (root / 'accepted').read_text() == store.token
    assert stat.S_IMODE((root / 'accepted').stat().st_mode) == 0o600
    first, other = store.open_client('first'), store.open_client('other')
    assert first[TOKEN_ENV] == store.token and first[DIRECTORY_ENV] != other[DIRECTORY_ENV]
    (Path(first[DIRECTORY_ENV]) / 'partial').write_text('unfinished')
    store.close_client('first')
    assert not Path(first[DIRECTORY_ENV]).exists() and Path(other[DIRECTORY_ENV]).exists()
    store.close(); store.close()
    assert not root.exists()


@pytest.mark.parametrize('challenge', ['', 'short', 'a' * 65, '../some-file'])
def test_malformed_challenge_never_enables_shared_staging(tmp_path, monkeypatch, challenge):
    root = tmp_path / 'staging'; root.mkdir(mode=0o700)
    (root / 'challenge').write_text(challenge)
    monkeypatch.setenv(ROOT_ENV, str(root))
    assert SharedArtifacts.negotiate() is None
    assert not (root / 'accepted').exists()
