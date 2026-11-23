import json
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from jusi.interfaces.http import ClientFollowupHandler


class TestImageSubmission(AsyncHTTPTestCase):
    def get_app(self):
        return Application([(r"/clients/(.*)", ClientFollowupHandler, {"supervisor": self})])

    def followup(self, *, attachments, **kwargs):
        self.paths = [Path(file["path"]) for file in attachments]
        assert attachments[0]["name"] == "a b.csv" and attachments[0]["media_type"] == "text/csv"
        assert self.paths[0].read_bytes() == self.image
        return {"received": len(attachments)}

    def test_target_staging_lifetime(self):
        self.image = b"city,value\r\nOslo,8\r\n"
        command = dict(protocol_version=1, command_id="cmd", trace_id="trace", kind="followup",
                       client_id="client", body="", attachment_count=1)
        body = b"\r\n".join([b"--boundary", b'Content-Disposition: form-data; name="command"', b"",
            json.dumps(command).encode(), b"--boundary", b'Content-Disposition: form-data; name="attachment"; filename="a%20b.csv"',
            b"Content-Type: text/csv", b"", self.image, b"--boundary--", b""])
        response = self.fetch("/clients/client", method="POST", body=body,
                              headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        assert response.code == 200, response.body
        assert json.loads(response.body)["received"] == 1
        assert not self.paths[0].exists()
