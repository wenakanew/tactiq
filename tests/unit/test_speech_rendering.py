import base64
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree

import pytest
import requests

from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult


def test_ssml_escapes_text_and_rejects_attribute_injection():
    valid = AzureSpeechSynthesizer._single_commentary_ssml(
        "A & B < C", "en-GB", "configured-voice", "+2%", "+1st"
    )
    root = ElementTree.fromstring(valid)
    assert "A & B < C" in "".join(root.itertext())
    with pytest.raises(ValueError):
        AzureSpeechSynthesizer._single_commentary_ssml("text", "en-GB", "voice' bad='x", "+2%", "+1st")
    with pytest.raises(ValueError):
        AzureSpeechSynthesizer._single_commentary_ssml("text", "en-GB", "voice", "+2000%", "+1st")


def test_region_only_rest_configuration_is_usable(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_SPEECH_API_KEY", "test-only")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    synthesizer = AzureSpeechSynthesizer()
    assert synthesizer.is_configured()
    assert synthesizer._speech_rest_urls("", "test-region") == [
        "https://test-region.tts.speech.microsoft.com/cognitiveservices/v1"
    ]


def test_metered_speech_never_retries_an_ambiguous_paid_call(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_API_KEY", "offline-only")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    calls = []

    def timeout(*args, **kwargs):
        calls.append(True)
        raise requests.Timeout("Ambiguous outcome")

    monkeypatch.setattr(requests, "post", timeout)
    with pytest.raises(RuntimeError, match="synthesis failed"):
        AzureSpeechSynthesizer(metered=True)._synthesize_with_rest("<speak/>", "offline-test")
    assert len(calls) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg integration requires the media runtime")
def test_dual_audio_is_decodable_media_not_concatenated_mp3_headers(tmp_path):
    binary = shutil.which("ffmpeg")
    source = tmp_path / "tone.mp3"
    subprocess.run([binary, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                    "-c:a", "libmp3lame", str(source)], check=True, timeout=10)
    combined = AzureSpeechSynthesizer._join_audio(source.read_bytes(), source.read_bytes())
    output = tmp_path / "duo.mp3"
    output.write_bytes(combined)
    result = subprocess.run([binary, "-v", "error", "-i", str(output), "-f", "null", "-"],
                            capture_output=True, timeout=10)
    assert result.returncode == 0
    assert len(combined) > 0
