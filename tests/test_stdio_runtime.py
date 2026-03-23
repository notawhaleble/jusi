import io
import unittest

from jusi.interfaces.protocol import parse_envelope
from jusi.interfaces.server import ProtocolServer
from jusi.interfaces.stdio import process_stream


class StdioRuntimeTest(unittest.TestCase):
    def test_process_stream_writes_line_delimited_responses(self) -> None:
        instream = io.StringIO(
            '{"version": 1, "kind": "request", "type": "start_session", '
            '"request_id": "req-1", "payload": {"notebook_id": "nb-1", "kernel_name": "python3"}}\n'
        )
        outstream = io.StringIO()

        rc = process_stream(instream, outstream, server=ProtocolServer())

        self.assertEqual(0, rc)
        lines = [line for line in outstream.getvalue().splitlines() if line.strip()]
        envelopes = [parse_envelope(line) for line in lines]
        self.assertEqual("response", envelopes[0].kind)
        self.assertTrue(envelopes[0].ok)
        self.assertEqual("session_updated", envelopes[1].type)
        self.assertEqual("prepared_updated", envelopes[3].type)


if __name__ == "__main__":
    unittest.main()
