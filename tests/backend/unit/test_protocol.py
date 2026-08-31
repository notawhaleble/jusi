from __future__ import annotations

import pytest

from jusi.protocol import ProtocolValidationError, validate_command


def test_validate_execute_command() -> None:
    command = validate_command(
        {
            "protocol_version": 1,
            "command_id": "cmd_1",
            "trace_id": "trace_1",
            "kind": "execute",
            "kernel_id": "krn_1",
            "notebook_id": "nb_1",
            "cell_id": "cell_1",
            "code": "1 + 1",
        },
        "execute",
    )
    assert command["code"] == "1 + 1"


@pytest.mark.parametrize(
    "mutation",
    [
        {"protocol_version": 2},
        {"trace_id": ""},
        {"kind": "stop_kernel"},
        {"extra": True},
    ],
)
def test_invalid_command_is_rejected(mutation: dict) -> None:
    command = {
        "protocol_version": 1,
        "command_id": "cmd_1",
        "trace_id": "trace_1",
        "kind": "start_kernel",
        "notebook_id": "nb_1",
        "kernel_name": "python3",
    }
    command.update(mutation)
    with pytest.raises(ProtocolValidationError):
        validate_command(command, "start_kernel")
