"""VieNeu wire contract and preservation of provider-specific speaker voices."""

import io
import shutil
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from commands.podcast_commands import apply_language_voice_preset
from notebooke.ai.models import AIFactory
from notebooke.ai.vieneu_tts_provider import VieNeuTTSProvider


def test_factory_routes_only_vieneu_compatible_models():
    model = AIFactory.create_text_to_speech(
        provider="openai_compatible",
        model_name="vieneu-v3-turbo",
        config={"base_url": "http://localhost:8000/v1"},
    )
    assert isinstance(model, VieNeuTTSProvider)
    assert "Ngọc Trân" in model.available_voices
    assert len(model.available_voices) == 25


def test_language_does_not_replace_vieneu_or_per_speaker_override():
    profile = {
        "tts_provider": "openai_compatible",
        "speakers": [{"voice_id": "Mai Anh"}],
    }
    apply_language_voice_preset(profile, "Vietnamese")
    assert profile["speakers"][0]["voice_id"] == "Mai Anh"
    mixed = {
        "tts_provider": "edgetts",
        "speakers": [
            {"voice_model": "model:vieneu", "voice_id": "Quang Sơn"},
            {"voice_id": "legacy"},
        ],
    }
    apply_language_voice_preset(mixed, "Vietnamese")
    assert mixed["speakers"][0]["voice_id"] == "Quang Sơn"
    assert mixed["speakers"][1]["voice_id"] == "vi-VN-HoaiMyNeural"


@pytest.mark.asyncio
async def test_vieneu_requests_wav_and_rejects_non_audio(tmp_path):
    provider = VieNeuTTSProvider(
        model_name="vieneu-v3-turbo",
        config={
            "base_url": "http://localhost:8000/v1",
            "api_key": "test-key",
        },
    )
    target = SimpleNamespace(
        url="http://127.0.0.1:8000/v1/audio/speech", headers={}, extensions={}
    )
    response = httpx.Response(
        200, content=b'{"error":"not audio"}', request=httpx.Request("POST", target.url)
    )
    post = AsyncMock(return_value=response)
    with (
        patch(
            "notebooke.ai.vieneu_tts_provider.prepare_pinned_http_target",
            AsyncMock(return_value=target),
        ),
        patch("httpx.AsyncClient.post", post),
    ):
        with pytest.raises(ValueError, match="invalid WAV"):
            await provider.agenerate_speech(
                "Xin chào", "Mai Anh", tmp_path / "speech.mp3"
            )
    assert post.call_args.kwargs["json"] == {
        "model": "vieneu-v3-turbo",
        "input": "Xin chào",
        "voice": "Mai Anh",
        "response_format": "wav",
    }
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert not (tmp_path / "speech.mp3").exists()


@pytest.mark.asyncio
async def test_vieneu_wav_is_encoded_as_playable_mp3(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for media generation")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x00\x00" * 4800)
    target = SimpleNamespace(
        url="http://127.0.0.1:8000/v1/audio/speech", headers={}, extensions={}
    )
    response = httpx.Response(
        200, content=buffer.getvalue(), request=httpx.Request("POST", target.url)
    )
    provider = VieNeuTTSProvider(
        model_name="vieneu-v3-turbo", config={"base_url": "http://localhost:8000/v1"}
    )
    output = tmp_path / "speech.mp3"
    with (
        patch(
            "notebooke.ai.vieneu_tts_provider.prepare_pinned_http_target",
            AsyncMock(return_value=target),
        ),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)),
    ):
        result = await provider.agenerate_speech("Xin chào", "Mai Anh", output)
    from pydub import AudioSegment

    assert result.audio_data == output.read_bytes()
    assert result.content_type == "audio/mpeg"
    assert len(AudioSegment.from_file(output, format="mp3")) == 100


@pytest.mark.asyncio
async def test_video_submission_keeps_selected_model_and_voice():
    from api.video_service import VideoService

    with (
        patch(
            "api.video_service.submit_command", Mock(return_value="command:test")
        ) as submit,
        patch(
            "notebooke.ai.models.Model.get",
            AsyncMock(
                return_value=SimpleNamespace(
                    type="text_to_speech", name="vieneu-v3-turbo"
                )
            ),
        ),
    ):
        await VideoService.submit_generation_job(
            name="Video",
            content="Nguồn dữ liệu",
            voice_model="model:vieneu",
            tts_voice="Quang Sơn",
        )
    assert submit.call_args.args[2]["voice_model"] == "model:vieneu"
    assert submit.call_args.args[2]["tts_voice"] == "Quang Sơn"


@pytest.mark.asyncio
async def test_video_submission_uses_preferred_vieneu_without_ui():
    from api.video_service import VideoService

    preferred = SimpleNamespace(
        id="model:vieneu", type="text_to_speech", name="vieneu-v3-turbo"
    )
    with (
        patch(
            "api.video_service.submit_command", Mock(return_value="command:test")
        ) as submit,
        patch(
            "notebooke.ai.models.DefaultModels.get_instance",
            AsyncMock(
                return_value=SimpleNamespace(
                    default_text_to_speech_model="model:vieneu"
                )
            ),
        ),
        patch("notebooke.ai.models.Model.get", AsyncMock(return_value=preferred)),
        patch(
            "notebooke.domain.content_settings.ContentSettings.get_instance",
            AsyncMock(return_value=SimpleNamespace(default_video_voice="Hải Đăng")),
        ),
    ):
        await VideoService.submit_generation_job(name="Video", content="Nguồn dữ liệu")
    assert submit.call_args.args[2]["voice_model"] == "model:vieneu"
    assert submit.call_args.args[2]["tts_voice"] == "Hải Đăng"


@pytest.mark.asyncio
async def test_voice_discovery_includes_enrolled_voices_and_metadata():
    provider = VieNeuTTSProvider(
        model_name="vieneu-v3-turbo",
        config={
            "base_url": "http://localhost:8001/v1",
            "api_key": "test-key",
        },
    )
    target = SimpleNamespace(
        url="http://127.0.0.1:8001/v1/voices", headers={}, extensions={}
    )
    response = httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "Giọng riêng",
                    "name": "Giọng riêng",
                    "gender": "female",
                    "description": "Nữ · Miền Nam",
                }
            ]
        },
        request=httpx.Request("GET", target.url),
    )
    get = AsyncMock(return_value=response)
    with (
        patch(
            "notebooke.ai.vieneu_tts_provider.prepare_pinned_http_target",
            AsyncMock(return_value=target),
        ),
        patch("httpx.AsyncClient.get", get),
    ):
        voices = await provider.alist_voices()
    assert [v.id for v in voices] == ["Giọng riêng"]
    assert voices[0].description == "Nữ · Miền Nam"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_voice_route_rejects_non_tts_model():
    from api.routers.models import list_model_voices
    from notebooke.exceptions import InvalidInputError

    with patch(
        "api.routers.models.Model.get",
        AsyncMock(return_value=SimpleNamespace(type="language")),
    ):
        with pytest.raises(InvalidInputError):
            await list_model_voices("model:language")
