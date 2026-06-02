"""CU-17: transcripción de audio a texto."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import httpx

from ..core.config import settings


def transcribe_audio_bytes(data: bytes, mime_type: str = "audio/aac") -> tuple[str, str]:
    if settings.openai_api_key:
        return _whisper_openai(data, mime_type), "openai-whisper"

    text = _transcribe_google(data, mime_type)
    if text:
        return text, "google-speech"

    raise RuntimeError(
        "Transcripción no disponible. Configure OPENAI_API_KEY o grabe con conexión a internet."
    )


def _whisper_openai(data: bytes, mime_type: str) -> str:
    ext = ".m4a" if "aac" in mime_type or "m4a" in mime_type else ".wav"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            data={"model": "whisper-1", "language": "es"},
            files={"file": (f"audio{ext}", data, mime_type)},
        )
        resp.raise_for_status()
        body = resp.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise RuntimeError("El audio no produjo texto reconocible")
        return text


def _transcribe_google(data: bytes, mime_type: str) -> str | None:
    try:
        import speech_recognition as sr
    except ImportError:
        return None

    wav_data = _to_wav_bytes(data, mime_type)
    if not wav_data:
        return None

    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(wav_data)) as source:
        audio = recognizer.record(source)
    try:
        return recognizer.recognize_google(audio, language="es-ES").strip()
    except Exception:
        return None


def _to_wav_bytes(data: bytes, mime_type: str) -> bytes | None:
    try:
        from pydub import AudioSegment
    except ImportError:
        return None

    suffix = ".m4a" if "aac" in mime_type or "m4a" in mime_type else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
        src.write(data)
        src_path = Path(src.name)
    out_path = src_path.with_suffix(".wav")
    try:
        seg = AudioSegment.from_file(src_path)
        seg.export(out_path, format="wav")
        return out_path.read_bytes()
    except Exception:
        return None
    finally:
        src_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
