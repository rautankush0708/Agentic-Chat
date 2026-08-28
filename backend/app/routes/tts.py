from flask import Blueprint, current_app, jsonify, request, send_file
import io

from ..services.tts_provider import TTSError, synthesize_speech

tts_bp = Blueprint("tts", __name__)


@tts_bp.post("/tts")
def text_to_speech():
    payload = request.get_json(silent=True) or {}
    text_value = (payload.get("text") or "").strip()
    if not text_value:
        return jsonify({"error": "`text` is required"}), 400

    try:
        audio_bytes, mime_type = synthesize_speech(current_app.config, text_value)
    except TTSError as exc:
        return jsonify({"error": str(exc)}), 502

    return send_file(io.BytesIO(audio_bytes), mimetype=mime_type)
