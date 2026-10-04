import os
from pathlib import Path

import requests

root = Path(__file__).resolve().parent
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

ssml = (
    "<speak version='1.0' xml:lang='en-GB' xmlns='http://www.w3.org/2001/10/synthesis'>"
    "<voice name='en-GB-RyanNeural'><prosody rate='+9%' pitch='+1st' volume='+2dB'>Quick line</prosody></voice>"
    "<break time='220ms'/>"
    "<voice name='en-GB-ThomasNeural'><prosody rate='-1%' pitch='-2st' volume='+0dB'>Another line</prosody></voice>"
    "</speak>"
)
headers = {
    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_API_KEY"],
    "Ocp-Apim-Subscription-Region": os.environ.get("AZURE_SPEECH_REGION", "southafricanorth"),
    "Content-Type": "application/ssml+xml",
    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    "User-Agent": "tactiq",
}

resp = requests.post(
    "https://southafricanorth.tts.speech.microsoft.com/cognitiveservices/v1",
    data=ssml.encode("utf-8"),
    headers=headers,
    timeout=30,
)
print(resp.status_code)
print(resp.text[:500])
print(len(resp.content))
