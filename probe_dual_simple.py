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
primary_voice = os.environ.get("TACTIQ_PROBE_PRIMARY_VOICE", "")
secondary_voice = os.environ.get("TACTIQ_PROBE_SECONDARY_VOICE", "")
primary_text = os.environ.get("TACTIQ_PROBE_PRIMARY_TEXT", "")
secondary_text = os.environ.get("TACTIQ_PROBE_SECONDARY_TEXT", "")
region = os.environ.get("AZURE_SPEECH_REGION", "")

if not language or not primary_voice or not secondary_voice:
    raise RuntimeError("TACTIQ_PROBE_LANGUAGE, TACTIQ_PROBE_PRIMARY_VOICE, and TACTIQ_PROBE_SECONDARY_VOICE must be set")
if not primary_text or not secondary_text:
    raise RuntimeError("TACTIQ_PROBE_PRIMARY_TEXT and TACTIQ_PROBE_SECONDARY_TEXT must be set")
if not region:
    raise RuntimeError("AZURE_SPEECH_REGION must be set")

ssml = (
    f"<speak version='1.0' xml:lang='{language}' xmlns='http://www.w3.org/2001/10/synthesis'>"
    f"<voice name='{primary_voice}'>{primary_text}</voice>"
    "<break time='220ms'/>"
    f"<voice name='{secondary_voice}'>{secondary_text}</voice>"
    "</speak>"
)
headers = {
    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_API_KEY"],
    "Ocp-Apim-Subscription-Region": region,
    "Content-Type": "application/ssml+xml",
    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    "User-Agent": "tactiq",
}

resp = requests.post(
    _speech_rest_url(),
    data=ssml.encode("utf-8"),
    headers=headers,
    timeout=30,
)
print(resp.status_code, len(resp.content), resp.text[:120])
