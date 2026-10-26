import threading
import time

import pytest

from tactiq.video import download


def test_slow_blocking_read_is_interrupted_by_the_total_deadline(monkeypatch):
    stopped = threading.Event()

    class Transport:
        def shutdown(self, how):
            stopped.set()

    class Response:
        status = 200

        def getheader(self, name):
            return "video/mp4" if name == "Content-Type" else None

        def read1(self, size):
            assert stopped.wait(timeout=2), "The watchdog failed to interrupt the stalled response"
            return b""

    class Connection:
        def __init__(self, *args):
            self.sock = Transport()

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    real_timer = threading.Timer
    monkeypatch.setattr(download, "public_addresses", lambda *args: ["93.184.216.34"])
    monkeypatch.setattr(download, "_PinnedHTTPS", Connection)
    monkeypatch.setattr(download, "Timer", lambda interval, function: real_timer(0.05, function))
    started = time.monotonic()
    with pytest.raises(ValueError, match="time budget"):
        download.download_video("https://video.example/match.mp4", 100)
    assert time.monotonic() - started < 2


def test_private_redirect_target_is_checked_before_connection(monkeypatch):
    checked = []

    def addresses(host, port):
        checked.append(host)
        if host == "private.example":
            raise ValueError("Video links must resolve exclusively to public addresses")
        return ["93.184.216.34"]

    class Redirect:
        status = 302

        def getheader(self, name):
            return "https://private.example/match.mp4" if name == "Location" else None

    class Connection:
        def __init__(self, *args):
            self.sock = None

        def connect(self):
            self.sock = self

        def shutdown(self, how):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Redirect()

        def close(self):
            pass

    monkeypatch.setattr(download, "public_addresses", addresses)
    monkeypatch.setattr(download, "_PinnedHTTPS", Connection)
    with pytest.raises(ValueError, match="public addresses"):
        download.download_video("https://video.example/match.mp4", 100)
    assert checked == ["video.example", "private.example"]
