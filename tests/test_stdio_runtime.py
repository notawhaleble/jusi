import io
import os
import threading
import unittest

from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer
from jusi.interfaces.stdio import process_stream
from jusi.infrastructure.runtime import InMemoryKernelRuntime


class StdioRuntimeTest(unittest.TestCase):
    def test_process_stream_writes_line_delimited_responses(self) -> None:
        instream = io.StringIO(
            '{"version": 1, "kind": "request", "type": "start_session", '
            '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}\n'
        )
        outstream = io.StringIO()

        rc = process_stream(instream, outstream, server=ProtocolServer(runtime=InMemoryKernelRuntime()))

        self.assertEqual(0, rc)
        lines = [line for line in outstream.getvalue().splitlines() if line.strip()]
        envelopes = [parse_envelope(line) for line in lines]
        self.assertEqual("response", envelopes[0].kind)
        self.assertTrue(envelopes[0].ok)
        self.assertEqual("session_updated", envelopes[1].type)
        self.assertEqual("session_updated", envelopes[2].type)

    def test_process_stream_drains_multiple_buffered_fd_requests(self) -> None:
        read_fd, write_fd = os.pipe()
        outstream = io.StringIO()

        def _writer() -> None:
            os.write(
                write_fd,
                (
                    '{"version": 1, "kind": "request", "type": "start_session", '
                    '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}\n'
                    '{"version": 1, "kind": "request", "type": "stop_session", '
                    '"request_id": "req-2", "payload": {"notebook_id": "nb-1", "session_id": "sess-missing"}}\n'
                ).encode("utf-8"),
            )
            os.close(write_fd)

        writer = threading.Thread(target=_writer)
        writer.start()
        with os.fdopen(read_fd, "r", encoding="utf-8") as instream:
            rc = process_stream(instream, outstream, server=ProtocolServer(runtime=InMemoryKernelRuntime()))
        writer.join()

        self.assertEqual(0, rc)
        lines = [line for line in outstream.getvalue().splitlines() if line.strip()]
        envelopes = [parse_envelope(line) for line in lines]
        response_ids = [envelope.request_id for envelope in envelopes if envelope.kind == "response"]
        self.assertEqual(["req-1", "req-2"], response_ids)

    def test_process_stream_closes_runtime_on_backend_exit(self) -> None:
        server = ProtocolServer(runtime=InMemoryKernelRuntime())
        instream = io.StringIO(
            '{"version": 1, "kind": "request", "type": "start_session", '
            '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}\n'
        )
        outstream = io.StringIO()

        rc = process_stream(instream, outstream, server=server)

        self.assertEqual(0, rc)
        lines = [line for line in outstream.getvalue().splitlines() if line.strip()]
        envelopes = [parse_envelope(line) for line in lines]
        session_id = envelopes[2].payload["session"]["id"]
        self.assertEqual([], server._runtime.list_clients(session_id))


if __name__ == "__main__":
    unittest.main()
