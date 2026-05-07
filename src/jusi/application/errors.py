from __future__ import annotations


class SessionError(ValueError):
    code = "invalid_state"


class SessionNotFoundError(SessionError):
    code = "session_not_found"


class SessionStoppedError(SessionError):
    code = "session_stopped"


class SessionExpiredError(SessionError):
    code = "session_expired"
