import os
from pathlib import Path

import requests

root = Path(__file__).resolve().parent
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

voices = [
    "en-GB-RyanNeural",
    "en-GB-ThomasNeural",
    "en-US-GuyNeural",
    "en-US-DavisNeural",
]

headers = {
    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_API_KEY"],
    "Ocp-Apim-Subscription-Region": os.environ.get("AZURE_SPEECH_REGION", "southafricanorth"),
    "Content-Type": "application/ssml+xml",
    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    "User-Agent": "tactiq",
}

for voice in voices:
    ssml = (
        "<speak version='1.0' xml:lang='en-GB' xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice name='{voice}'>Voice check {voice}</voice>"
        "</speak>"
    )
    resp = requests.post(
        "https://southafricanorth.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    print(voice, resp.status_code, len(resp.content))
