import os
from pathlib import Path
from urllib.parse import urlparse

import requests

root = Path(__file__).resolve().parent


def _load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _speech_rest_url() -> str:
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    endpoint = os.environ.get("AZURE_SPEECH_ENDPOINT", "").strip()
    if region:
        return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    if endpoint:
        parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
        return f"{parsed.scheme}://{parsed.netloc}/cognitiveservices/v1"
    raise RuntimeError("Set AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT")


_load_dotenv_if_present(root / ".env")

language = os.environ.get("TACTIQ_PROBE_LANGUAGE", "")
voices_raw = os.environ.get("TACTIQ_PROBE_VOICE_LIST", "")
region = os.environ.get("AZURE_SPEECH_REGION", "")

if not language:
    raise RuntimeError("TACTIQ_PROBE_LANGUAGE must be set")
if not voices_raw.strip():
    raise RuntimeError("TACTIQ_PROBE_VOICE_LIST must be set (comma separated)")
if not region:
    raise RuntimeError("AZURE_SPEECH_REGION must be set")

voices = [item.strip() for item in voices_raw.split(",") if item.strip()]

headers = {
    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_API_KEY"],
    "Ocp-Apim-Subscription-Region": region,
    "Content-Type": "application/ssml+xml",
    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    "User-Agent": "tactiq",
}

for voice in voices:
    ssml = (
        f"<speak version='1.0' xml:lang='{language}' xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice name='{voice}'>Voice check {voice}</voice>"
        "</speak>"
    )
    resp = requests.post(
        _speech_rest_url(),
        data=ssml.encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    print(voice, resp.status_code, len(resp.content))
