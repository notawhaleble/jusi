import unittest
from unittest.mock import patch

from jusi.infrastructure.client_runtime_host import (
    build_registered_runtime_modes,
    build_runtime_host_factories,
    build_transcript_runtime_host_factory,
    default_launch_runtime_mode,
    default_transition_target_runtime_mode,
    RegisteredRuntimeMode,
    get_registered_runtime_mode,
    run_client_runtime_mode,
)


class ClientRuntimeHostTest(unittest.TestCase):
    def test_run_client_runtime_mode_dispatches_to_host(self) -> None:
        calls: list[str] = []

        class FakeHost:
            def __init__(self, name: str) -> None:
                self._name = name

            def run(self) -> int:
                calls.append(self._name)
                return 11 if self._name == "transcript" else 17

        rc = run_client_runtime_mode(
            "transcript",
            host_factories={
                "transcript": lambda: FakeHost("transcript"),
                "handler": lambda: FakeHost("handler"),
            },
        )
        self.assertEqual(11, rc)
        self.assertEqual(["transcript"], calls)

    def test_run_client_runtime_mode_rejects_unknown_mode(self) -> None:
        rc = run_client_runtime_mode("nope", host_factories={})
        self.assertEqual(2, rc)

    def test_named_mode_helpers_return_callable_factories(self) -> None:
        self.assertTrue(callable(build_transcript_runtime_host_factory()))

    def test_registered_runtime_modes_build_factory_mapping(self) -> None:
        modes = build_registered_runtime_modes()
        factories = build_runtime_host_factories(modes)
        self.assertIn("transcript", factories)
        self.assertNotIn("handler", factories)

    def test_registered_runtime_modes_expose_backend_semantics(self) -> None:
        transcript = get_registered_runtime_mode("transcript")
        handler = get_registered_runtime_mode("handler")
        self.assertIsNotNone(transcript)
        self.assertIsNotNone(handler)
        self.assertEqual("kernel", transcript.owner_kind if transcript is not None else "")
        self.assertFalse(transcript.accepts_frontend_messages if transcript is not None else True)
        self.assertFalse(transcript.requires_handler_id if transcript is not None else True)
        self.assertFalse(transcript.uses_live_controller if transcript is not None else True)
        self.assertTrue(transcript.supports_direct_launch() if transcript is not None else False)
        self.assertEqual((), transcript.allowed_transition_sources if transcript is not None else ("x",))
        self.assertIsNone(transcript.transition_factory if transcript is not None else object())
        self.assertEqual("handler", handler.owner_kind if handler is not None else "")
        self.assertTrue(handler.accepts_frontend_messages if handler is not None else False)
        self.assertTrue(handler.requires_handler_id if handler is not None else False)
        self.assertTrue(handler.uses_live_controller if handler is not None else False)
        self.assertFalse(handler.supports_direct_launch() if handler is not None else True)
        self.assertEqual(("transcript",), handler.allowed_transition_sources if handler is not None else ())
        self.assertIsNotNone(handler.transition_factory if handler is not None else None)

    def test_default_launch_runtime_mode_returns_transcript(self) -> None:
        mode = default_launch_runtime_mode()
        self.assertEqual("transcript", mode.name)

    def test_default_transition_target_runtime_mode_returns_handler_for_transcript(self) -> None:
        mode = default_transition_target_runtime_mode("transcript")
        self.assertEqual("handler", mode.name)

    def test_default_transition_target_runtime_mode_rejects_missing_transition_target(self) -> None:
        patched_modes = []
        for mode in build_registered_runtime_modes():
            if mode.name == "handler":
                patched_modes.append(
                    RegisteredRuntimeMode(
                        name=mode.name,
                        owner_kind=mode.owner_kind,
                        accepts_frontend_messages=mode.accepts_frontend_messages,
                        requires_handler_id=mode.requires_handler_id,
                        uses_live_controller=mode.uses_live_controller,
                        host_factory=mode.host_factory,
                        allowed_transition_sources=(),
                        transition_factory=mode.transition_factory,
                    )
                )
            else:
                patched_modes.append(mode)

        with patch(
            "jusi.infrastructure.client_runtime_host.build_registered_runtime_modes",
            return_value=tuple(patched_modes),
        ):
            with self.assertRaisesRegex(ValueError, "No registered runtime mode allows transition from transcript"):
                default_transition_target_runtime_mode("transcript")


if __name__ == "__main__":
    unittest.main()
