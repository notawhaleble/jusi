import unittest

from jusi.infrastructure.client_runtime_controller import ActiveClientController, LiveClientControllerRegistry


class _ProbeController:
    def __init__(self) -> None:
        self.messages: list[tuple[object, str, dict]] = []
        self.interrupted = False
        self.stopped = False

    def on_frontend_message(self, context: object, message_type: str, payload: dict) -> None:
        self.messages.append((context, message_type, dict(payload)))

    def interrupt(self) -> None:
        self.interrupted = True

    def stop(self) -> None:
        self.stopped = True


class ClientRuntimeControllerTest(unittest.TestCase):
    def test_replace_stops_previous_runtime_controller(self) -> None:
        registry = LiveClientControllerRegistry()
        previous = _ProbeController()
        current = _ProbeController()
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=previous,
                runtime_mode="transcript",
            ),
        )

        replaced = registry.replace(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=current,
                runtime_mode="handler",
                handler_id="vd",
            ),
        )

        self.assertIsNotNone(replaced)
        self.assertTrue(previous.stopped)
        active = registry.get("sess-1", "client-1")
        self.assertIsNotNone(active)
        self.assertIs(active.controller if active is not None else None, current)

    def test_remove_client_stops_registered_runtime_controller(self) -> None:
        registry = LiveClientControllerRegistry()
        controller = _ProbeController()
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=controller,
                runtime_mode="transcript",
            ),
        )

        registry.remove_client("sess-1", "client-1")

        self.assertTrue(controller.stopped)
        self.assertIsNone(registry.get("sess-1", "client-1"))

    def test_dispatch_frontend_message_rejects_non_handler_mode(self) -> None:
        registry = LiveClientControllerRegistry()
        controller = _ProbeController()
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=controller,
                runtime_mode="transcript",
                handler_id="vd",
            ),
        )

        with self.assertRaisesRegex(ValueError, "does not accept handler messages"):
            registry.dispatch_frontend_message(
                "sess-1",
                "client-1",
                handler_id="vd",
                message_type="followup",
                payload={"cell_text": "x"},
            )
        self.assertEqual([], controller.messages)

    def test_dispatch_frontend_message_routes_handler_mode(self) -> None:
        registry = LiveClientControllerRegistry()
        controller = _ProbeController()
        registry.register(
            "sess-1",
            "client-1",
            ActiveClientController(
                client_id="client-1",
                controller=controller,
                runtime_mode="handler",
                handler_id="vd",
                context={"x": 1},
            ),
        )

        active = registry.dispatch_frontend_message(
            "sess-1",
            "client-1",
            handler_id="vd",
            message_type="followup",
            payload={"cell_text": "x"},
        )

        self.assertEqual("handler", active.runtime_mode)
        self.assertEqual([({"x": 1}, "followup", {"cell_text": "x"})], controller.messages)


if __name__ == "__main__":
    unittest.main()
