"""Text-to-speech provider abstraction.

Default is gTTS (Google Translate's free TTS endpoint - no API key required), so the
chat's "read the answer aloud" feature works out of the box. If SARVAM_API_KEY is set
and TTS_PROVIDER=sarvam, Sarvam AI's REST text-to-speech endpoint is used instead
(matching the vendor used in the original Angular reference implementation).
"""

import base64
import io

import requests


class TTSError(RuntimeError):
    pass


def synthesize_speech(config, text: str) -> tuple[bytes, str]:
    """Returns (audio_bytes, mime_type)."""
    if config["TTS_PROVIDER"] == "sarvam" and config["SARVAM_API_KEY"]:
        return _synthesize_sarvam(config, text)
    return _synthesize_gtts(text)


def _synthesize_gtts(text: str) -> tuple[bytes, str]:
    from gtts import gTTS

    buf = io.BytesIO()
    try:
        gTTS(text=text, lang="en").write_to_fp(buf)
    except Exception as exc:  # gTTS raises its own exception types on network failure
        raise TTSError(f"gTTS synthesis failed: {exc}") from exc
    return buf.getvalue(), "audio/mpeg"


def _synthesize_sarvam(config, text: str) -> tuple[bytes, str]:
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "API-Subscription-Key": config["SARVAM_API_KEY"],
        "Content-Type": "application/json",
    }
    body = {
        "inputs": [text],
        "target_language_code": config["SARVAM_TTS_LANGUAGE"],
        "speaker": config["SARVAM_TTS_SPEAKER"],
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TTSError(f"Sarvam TTS request failed: {exc}") from exc

    data = resp.json()
    audios = data.get("audios") or []
    if not audios:
        raise TTSError(f"Sarvam TTS returned no audio: {data}")
    return base64.b64decode(audios[0]), "audio/wav"
