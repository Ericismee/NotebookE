from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from esperanto.providers.tts.base import AudioResponse

from notebooke.ai.edge_tts_provider import EdgeTTSProvider
from notebooke.ai.speech import list_speech_voices, preview_speech, resolve_speech_model
from notebooke.exceptions import InvalidInputError


@pytest.fixture(autouse=True)
def isolated_voice_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("notebooke.ai.voice_samples.PREVIEW_FOLDER", tmp_path / "samples")


@pytest.mark.asyncio
async def test_english_switches_from_vieneu_to_native_voice_model():
    vieneu = SimpleNamespace(
        id="model:vi", type="text_to_speech", name="vieneu-v3-turbo"
    )
    edge = SimpleNamespace(
        id="model:en", type="text_to_speech", name="edge-tts", provider="edgetts"
    )
    with (
        patch("notebooke.ai.speech.Model.get", AsyncMock(return_value=vieneu)),
        patch(
            "notebooke.ai.speech.Model.get_models_by_type",
            AsyncMock(return_value=[edge]),
        ),
        patch(
            "notebooke.ai.speech.model_manager.get_model",
            AsyncMock(return_value=EdgeTTSProvider(model_name="edge-tts")),
        ),
    ):
        voices = await list_speech_voices("model:vi", "English")
    assert len(voices) == 5
    assert all(
        v["language_code"].startswith("en") and v["model_id"] == "model:en"
        for v in voices
    )


@pytest.mark.asyncio
async def test_preview_rejects_language_mismatch_without_synthesis():
    generate = AsyncMock()
    with (
        patch(
            "notebooke.ai.speech.list_speech_voices",
            AsyncMock(
                return_value=[{"id": "en-US-JennyNeural", "model_id": "model:en"}]
            ),
        ),
        patch("notebooke.ai.speech.model_manager.get_model", generate),
    ):
        with pytest.raises(InvalidInputError):
            await preview_speech("model:en", "Mai Anh", "en")
    generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("language, expected", [("en", "Every great idea"), ("vi", "Chào bạn")])
async def test_preview_uses_fixed_text_in_selected_language(language, expected):
    model = SimpleNamespace(
        agenerate_speech=AsyncMock(return_value=AudioResponse(audio_data=b"audio"))
    )
    with (
        patch(
            "notebooke.ai.speech.list_speech_voices",
            AsyncMock(return_value=[{"id": "voice", "model_id": "model:selected"}]),
        ),
        patch(
            "notebooke.ai.speech.model_manager.get_model", AsyncMock(return_value=model)
        ),
    ):
        result = await preview_speech("model:requested", "voice", language)
    assert result.audio_data == b"audio"
    assert model.agenerate_speech.call_args.kwargs["text"].startswith(expected)
    assert model.agenerate_speech.call_args.kwargs["voice"] == "voice"


@pytest.mark.asyncio
async def test_vietnamese_keeps_selected_vieneu_model():
    model = SimpleNamespace(
        id="model:vi", type="text_to_speech", name="vieneu-v3-turbo"
    )
    with patch("notebooke.ai.speech.Model.get", AsyncMock(return_value=model)):
        assert await resolve_speech_model("model:vi", "Tiếng Việt") is model


def test_selected_english_speaker_voice_is_not_replaced_by_preset():
    from commands.podcast_commands import apply_language_voice_preset

    profile = {
        "tts_provider": "edgetts",
        "speakers": [
            {"voice_id": "en-US-EmmaMultilingualNeural"},
            {"voice_id": "en-US-AndrewMultilingualNeural"},
        ],
    }
    apply_language_voice_preset(profile, "English", preserve_selected=True)
    assert [speaker["voice_id"] for speaker in profile["speakers"]] == [
        "en-US-EmmaMultilingualNeural",
        "en-US-AndrewMultilingualNeural",
    ]


@pytest.mark.asyncio
async def test_video_job_gets_language_matching_model_and_voice():
    from unittest.mock import Mock

    from api.video_service import VideoService

    model = SimpleNamespace(
        id="model:vi", type="text_to_speech", name="vieneu-v3-turbo"
    )
    with (
        patch("notebooke.ai.models.Model.get", AsyncMock(return_value=model)),
        patch(
            "notebooke.ai.speech.list_speech_voices",
            AsyncMock(
                return_value=[{"id": "en-US-JennyNeural", "model_id": "model:en"}]
            ),
        ),
        patch(
            "api.video_service.submit_command", Mock(return_value="command:test")
        ) as submit,
    ):
        await VideoService.submit_generation_job(
            name="Video",
            content="Research notes",
            voice_model="model:vi",
            tts_voice="Mai Anh",
            language="English",
        )
    args = submit.call_args.args[2]
    assert args["voice_model"] == "model:en"
    assert args["tts_voice"] == "en-US-JennyNeural"
    assert args["language"] == "English"
