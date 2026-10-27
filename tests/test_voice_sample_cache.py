import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from esperanto.providers.tts.base import AudioResponse

from notebooke.ai import voice_samples


@pytest.mark.asyncio
async def test_concurrent_previews_synthesize_once_and_survive_memory_reset(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(voice_samples, "PREVIEW_FOLDER", tmp_path)
    model = SimpleNamespace(
        agenerate_speech=AsyncMock(
            return_value=AudioResponse(
                audio_data=b"mp3 audio", content_type="audio/mpeg"
            )
        )
    )
    key = voice_samples.sample_key("model:vi", "Mai Anh", "vi")
    results = await asyncio.gather(
        *[voice_samples.prepare_sample(key, model, "Mai Anh", "vi") for _ in range(3)]
    )
    assert all(result.audio_data == b"mp3 audio" for result in results)
    model.agenerate_speech.assert_awaited_once()
    voice_samples._locks.clear()
    await voice_samples.prepare_sample(key, model, "Mai Anh", "vi")
    model.agenerate_speech.assert_awaited_once()
    assert (
        voice_samples.sample_metadata("model:vi", "Mai Anh", "vi")["preview_ready"]
        is True
    )


def test_changing_sentence_or_model_revision_invalidates_sample(monkeypatch):
    old = voice_samples.sample_key("model:vi", "Mai Anh", "vi", "v1")
    assert old != voice_samples.sample_key("model:vi", "Mai Anh", "vi", "v2")
    monkeypatch.setitem(voice_samples.SAMPLE_TEXT, "vi", "Một câu thoại mới.")
    assert old != voice_samples.sample_key("model:vi", "Mai Anh", "vi", "v1")
