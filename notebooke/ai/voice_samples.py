"""Versioned, local MP3 samples shared across preview requests and restarts."""

import asyncio
import hashlib
import io
import json
import uuid
from pathlib import Path

from esperanto.providers.tts.base import AudioResponse

from notebooke.config import DATA_FOLDER

PREVIEW_FOLDER = Path(DATA_FOLDER) / "voice_previews"
SAMPLE_TEXT = {
    "vi": "Chào bạn, mỗi ý tưởng đều có một câu chuyện đáng được kể. Hãy cùng mình khám phá những điều thú vị, qua từng trang sách và từng khoảnh khắc cuộc sống.",
    "en": "Every great idea begins with a little curiosity. Join me as we explore inspiring stories, discover fresh perspectives, and bring new ideas to life.",
}
_locks: dict[str, asyncio.Lock] = {}


def sample_key(model_id: str, voice: str, language: str, revision: str = "") -> str:
    data = json.dumps(
        [model_id, voice, language, revision, SAMPLE_TEXT[language]], ensure_ascii=False
    )
    return hashlib.sha256(data.encode()).hexdigest()


def sample_path(key: str) -> Path:
    # Keys are internal SHA-256 values, never user-supplied filesystem paths.
    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid voice sample key")
    return PREVIEW_FOLDER / f"{key}.mp3"


def sample_metadata(
    model_id: str, voice: str, language: str, revision: str = ""
) -> dict:
    language = language.lower().split("-")[0]
    if language not in SAMPLE_TEXT:
        return {"preview_key": None, "preview_ready": False}
    key = sample_key(model_id, voice, language, revision)
    return {"preview_key": key, "preview_ready": sample_path(key).is_file()}


async def cached_sample(key: str) -> AudioResponse | None:
    path = sample_path(key)
    if not path.is_file():
        return None
    data = await asyncio.to_thread(path.read_bytes)
    return AudioResponse(audio_data=data, content_type="audio/mpeg") if data else None


async def prepare_sample(key: str, model, voice: str, language: str):
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = await cached_sample(key)
        if cached:
            return cached
        audio = await model.agenerate_speech(text=SAMPLE_TEXT[language], voice=voice)
        if not audio.audio_data:
            raise ValueError("Speech provider returned empty audio")

        def persist():
            data = audio.audio_data
            if audio.content_type not in {"audio/mpeg", "audio/mp3"}:
                from pydub import AudioSegment

                output = io.BytesIO()
                AudioSegment.from_file(io.BytesIO(data)).export(output, format="mp3")
                data = output.getvalue()
            path = sample_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return data

        data = await asyncio.to_thread(persist)
        return AudioResponse(audio_data=data, content_type="audio/mpeg", voice=voice)
