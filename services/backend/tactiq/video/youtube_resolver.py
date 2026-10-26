"""Credential-free metadata resolution; media bytes are fetched by the pinned downloader."""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import socket
from urllib.parse import urlsplit


def provider_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in ("youtube.com", "googlevideo.com", "googleapis.com", "google.com", "ytimg.com"))


def format_metadata(item: dict) -> dict:
    from yt_dlp.utils import determine_protocol

    keys = ("format_id", "url", "ext", "vcodec", "acodec", "width", "height", "fps",
            "filesize", "filesize_approx", "abr", "tbr", "has_drm")
    result = {key: item[key] for key in keys if key in item}
    result["protocol"] = determine_protocol(item)
    if item.get("fragments"):
        result["fragments"] = True
    return result


def restrict_network() -> None:
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect

    def resolve(host, port, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                raise ValueError("Provider network policy rejected a non-public address")
        except ValueError as exc:
            if not isinstance(host, str) or not provider_host(host.rstrip(".").lower()):
                raise ValueError("Provider network policy rejected the host") from exc
        if int(port) != 443:
            raise ValueError("Provider metadata requires HTTPS")
        results = original_resolve(host, port, *args, **kwargs)
        if not results or any(not ipaddress.ip_address(item[4][0]).is_global for item in results):
            raise ValueError("Provider network policy rejected non-public DNS results")
        return results

    def connect(connection, address):
        if not isinstance(address, tuple):
            raise ValueError("Provider network policy rejected the connection")
        host, port = address[:2]
        results = resolve(host, port, type=socket.SOCK_STREAM)
        return original_connect(connection, results[0][4])

    socket.getaddrinfo = resolve
    socket.socket.connect = connect


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", args.video_id):
        raise ValueError("Invalid video identifier")
    restrict_network()
    import yt_dlp
    from yt_dlp.globals import all_plugins_loaded, plugin_dirs
    from yt_dlp.networking._urllib import UrllibRH
    from yt_dlp.utils import DownloadError

    plugin_dirs.value = []
    all_plugins_loaded.value = True
    options = yt_dlp.parse_options([
        "--ignore-config", "--no-plugin-dirs", "--no-remote-components", "--no-cache-dir",
        "--no-playlist", "--no-geo-bypass", "--proxy", "",
        "--socket-timeout", "10", "--retries", "0", "--extractor-retries", "0",
        "--js-runtimes", f"node:{shutil.which('node')}", "--quiet", "--no-warnings",
    ]).ydl_opts
    options.update({"allowed_extractors": ["youtube"], "skip_download": True, "cookiefile": None,
                    "cookiesfrombrowser": None, "usenetrc": False, "postprocessors": [], "enable_file_urls": False})

    class PublicYoutube(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            # Optional native transports must not bypass the process-local IP policy.
            return super().build_request_director([UrllibRH], preferences)

        def urlopen(self, request):
            url = request if isinstance(request, str) else getattr(request, "url", None) or request.get_full_url()
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not provider_host((parsed.hostname or "").lower()) or parsed.username or parsed.password:
                raise ValueError("Provider network policy rejected the request")
            return super().urlopen(request)

    try:
        with PublicYoutube(options) as client:
            info = client.extract_info(f"https://www.youtube.com/watch?v={args.video_id}",
                                       download=False, process=False, ie_key="Youtube")
        if not isinstance(info, dict) or info.get("_type", "video") != "video":
            raise ValueError("Provider did not return a single video")
        formats = []
        for item in info.get("formats", [])[:256]:
            if isinstance(item, dict) and len(item.get("url", "")) <= 8192:
                formats.append(format_metadata(item))
        result = {key: info.get(key) for key in ("id", "duration", "is_live", "live_status", "availability")}
        result["formats"] = formats
        print(json.dumps(result, allow_nan=False))
        return 0
    except (DownloadError, ValueError, OSError, RuntimeError) as error:
        message = str(error).lower()
        category = (
            "protected" if "drm" in message else "access_required" if any(
                word in message for word in ("sign in", "sign-in", "login", "private", "members", "cookies", "confirm"))
            else "network_policy" if "network policy" in message
            else "unavailable" if "unavailable" in message else "resolver_failed"
        )
        print(json.dumps({"error": category, "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
