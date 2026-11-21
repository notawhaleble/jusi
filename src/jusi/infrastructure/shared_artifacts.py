"""Optional shared staging negotiated with an owned local-service launcher.

Paths stay in process launch environment. Wire actions use an opaque artifact ID
inside the requesting client's namespace. The frontend adopts completed snapshots
out of staging before opening them, so independent views survive service cleanup.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

ROOT_ENV = 'JUSI_SHARED_ARTIFACT_ROOT'
DIRECTORY_ENV = 'JUSI_DOCUMENT_STAGE_DIR'
TOKEN_ENV = 'JUSI_DOCUMENT_STAGE_TOKEN'


class SharedArtifacts:
    def __init__(self, directory: Path, token: str):
        self.directory, self.token = directory, token

    @classmethod
    def negotiate(cls):
        value = os.environ.get(ROOT_ENV)
        if not value:
            return None
        try:
            directory = Path(value)
            info = directory.lstat()
            if not directory.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return None
            challenge = directory / 'challenge'
            if not stat.S_ISREG(challenge.lstat().st_mode):
                return None
            with challenge.open('r', encoding='ascii') as stream:
                token = stream.read(65)
            if re.fullmatch('[0-9a-f]{64}', token) is None:
                return None
            fd = os.open(directory / 'accepted', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(token)
            return cls(directory, token)
        except (OSError, ValueError, UnicodeError):
            return None  # A wrapper/container may have no access. HTTP still works.

    def client_directory(self, client_id):
        return self.directory / hashlib.sha256(client_id.encode()).hexdigest()

    def open_client(self, client_id):
        directory = self.client_directory(client_id)
        try:
            directory.mkdir(mode=0o700)
        except OSError:
            return {DIRECTORY_ENV: '', TOKEN_ENV: ''}
        return {DIRECTORY_ENV: str(directory), TOKEN_ENV: self.token}

    def close_client(self, client_id):
        shutil.rmtree(self.client_directory(client_id), ignore_errors=True)

    def close(self):
        # This exact private directory was offered by our process owner.
        shutil.rmtree(self.directory, ignore_errors=True)
