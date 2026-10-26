from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from threading import Event, Timer
from urllib.parse import quote, urljoin, urlsplit


def public_addresses(hostname: str, port: int) -> list[str]:
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)},
                           key=lambda address: (ipaddress.ip_address(address).version, address))
    except socket.gaierror as exc:
        raise ValueError("Unable to resolve video host") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Video links must resolve exclusively to public addresses")
    return addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def download_video(
    url: str, max_bytes: int, max_redirects: int = 4, *,
    allow_audio: bool = False, deadline: float | None = None,
    allowed_host_suffix: str | None = None,
) -> bytes:
    current = url
    deadline = min(deadline, time.monotonic() + 60) if deadline is not None else time.monotonic() + 60
    for _ in range(max_redirects + 1):
        parsed = urlsplit(current)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Video URL must be HTTP(S), with a host and no embedded credentials")
        hostname = parsed.hostname.lower().rstrip(".")
        if allowed_host_suffix is not None and (
            parsed.scheme != "https" or not (hostname == allowed_host_suffix or hostname.endswith("." + allowed_host_suffix))
        ):
            raise ValueError("Provider media redirected outside the permitted HTTPS hosts")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("Invalid video URL port") from exc
        addresses = public_addresses(parsed.hostname, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Video download exceeded its time budget")
        connection_type = _PinnedHTTPS if parsed.scheme == "https" else _PinnedHTTP
        # The dialled address is the one checked above. TLS still verifies the original hostname.
        connection = connection_type(parsed.hostname, addresses[0], port, min(15, remaining))
        expired = Event()
        watchdog = None
        try:
            connection.connect()
            transport = connection.sock
            assert transport is not None

            def abort_download(transport=transport, expired=expired) -> None:
                expired.set()
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    # A completed/closed connection needs no further interruption.
                    pass

            watchdog = Timer(max(0, deadline - time.monotonic()), abort_download)
            watchdog.daemon = True
            watchdog.start()
            path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                path += "?" + quote(parsed.query, safe="=&%/:@!$'()*+,;?-._~")
            connection.request("GET", path, headers={"User-Agent": "tactiq-video-ingest", "Accept": "audio/*" if allow_audio else "video/*"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Video redirect did not provide a destination")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise ValueError(f"Video URL returned HTTP {response.status}")
            content_type = (response.getheader("Content-Type") or "").split(";")[0].lower()
            if (not content_type.startswith("video/") and content_type != "application/octet-stream"
                    and not (allow_audio and content_type.startswith("audio/"))):
                raise ValueError("URL must return a direct video file, not a web page")
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_bytes or int(length) < 0:
                        raise ValueError("Video size exceeds the configured limit")
                except ValueError as exc:
                    raise ValueError("Invalid or oversized video Content-Length") from exc
            data = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("Video download exceeded its time budget")
                chunk = response.read1(min(1024 * 1024, max_bytes - len(data) + 1))
                if expired.is_set() or time.monotonic() > deadline:
                    raise ValueError("Video download exceeded its time budget")
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Video size exceeds the configured limit")
            if not data:
                raise ValueError("Downloaded video is empty")
            return bytes(data)
        except (OSError, http.client.HTTPException) as exc:
            if expired.is_set():
                raise ValueError("Video download exceeded its time budget") from exc
            raise ValueError("Unable to download video from the public host") from exc
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()
    raise ValueError("Too many video redirects")
