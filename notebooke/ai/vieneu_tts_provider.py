"""VieNeu's OpenAI speech endpoint, with WAV to MP3 conversion for media jobs."""

import asyncio
import io
import os
from pathlib import Path

import httpx
from esperanto.providers.tts.base import AudioResponse, Model, Voice

from notebooke.ai.edge_tts_provider import EdgeTTSProvider
from notebooke.exceptions import ConfigurationError
from notebooke.utils.url_validation import prepare_pinned_http_target


class VieNeuTTSProvider(EdgeTTSProvider):
    """Use a separately hosted VieNeu server; no model weights in the API worker."""

    PROVIDER = "openai-compatible"

    @property
    def available_voices(self):
        # SDK 3.8.3 preset identifiers. Custom enrolled voices are also accepted.
        males = [
            "Hải Đăng",
            "Thiện Minh",
            "Minh Đức",
            "Quốc Tuấn",
            "Adam bựa",
            "Thiền Tâm Đức",
            "Phạm Tuyên",
            "Thanh Bình",
            "Xuân Vĩnh",
            "Quang Sơn",
            "Adam",
            "Minh Triết",
            "Thái Sơn",
            "Đức Trí",
        ]
        females = [
            "Mai Anh",
            "Ngọc Huyền",
            "Ngọc Linh",
            "Quỳnh Anh",
            "Trúc Ly",
            "Đoan Trang",
            "Ngọc Trân",
            "Kim Thanh",
            "Mỹ Duyên",
            "Thùy Dung",
            "Thục Đoan",
        ]
        return {
            name: Voice(id=name, name=name, gender=gender, language_code="vi-VN")
            for names, gender in [(males, "MALE"), (females, "FEMALE")]
            for name in names
        }

    def _get_models(self):
        return [
            Model(
                id=self.model_name or "vieneu-v3-turbo",
                owned_by="vieneu",
                type="text_to_speech",
            )
        ]

    async def alist_voices(self):
        """Discover the server's presets and enrolled voices, including metadata."""
        config = self.config or {}
        base_url = (
            config.get("base_url")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL_TTS")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        )
        if not base_url:
            raise ConfigurationError("Configure the VieNeu OpenAI-compatible base URL")
        target = await prepare_pinned_http_target(
            str(base_url).rstrip("/") + "/voices", self.PROVIDER
        )
        headers = dict(target.headers)
        api_key = (
            config.get("api_key")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY_TTS")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY")
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                target.url, headers=headers, extensions=target.extensions
            )
            response.raise_for_status()
        return [
            Voice(
                id=v["id"],
                name=v.get("name") or v["id"],
                gender=v.get("gender") or "UNKNOWN",
                language_code="vi-VN",
                description=v.get("description"),
            )
            for v in response.json().get("data", [])
            if isinstance(v.get("id"), str) and v["id"]
        ]

    async def agenerate_speech(self, text, voice, output_file=None, **kwargs):
        config = {**(self.config or {}), **kwargs}
        if not text.strip() or not voice.strip():
            raise ValueError("Speech text and voice are required")
        base_url = (
            config.get("base_url")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL_TTS")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        )
        if not base_url:
            raise ConfigurationError("Configure the VieNeu OpenAI-compatible base URL")
        target = await prepare_pinned_http_target(
            str(base_url).rstrip("/") + "/audio/speech", self.PROVIDER
        )
        headers = dict(target.headers)
        api_key = (
            config.get("api_key")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY_TTS")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY")
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(
                target.url,
                headers=headers,
                extensions=target.extensions,
                json={
                    "model": self.model_name,
                    "input": text,
                    "voice": voice,
                    "response_format": "wav",
                },
            )
            response.raise_for_status()
        wav = response.content
        if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
            raise ValueError("VieNeu returned invalid WAV audio")

        def encode():
            from pydub import AudioSegment

            audio = AudioSegment.from_file(io.BytesIO(wav), format="wav")
            mp3 = io.BytesIO()
            audio.export(mp3, format="mp3")
            data = mp3.getvalue()
            if output_file:
                path = Path(output_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return data

        audio_data = await asyncio.to_thread(encode)
        return AudioResponse(
            audio_data=audio_data,
            content_type="audio/mpeg",
            model=self.model_name,
            voice=voice,
            provider=self.PROVIDER,
        )
