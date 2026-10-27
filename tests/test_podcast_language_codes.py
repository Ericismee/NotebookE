from unittest.mock import AsyncMock, patch

import pytest

from notebooke.podcasts.language import normalize_podcast_language
from notebooke.podcasts.models import EpisodeProfile


@pytest.mark.parametrize(
    "value,code",
    [
        ("Vietnamese", "vi"),
        ("Tiếng Việt", "vi"),
        ("English", "en"),
        ("Tiếng Anh", "en"),
        ("vi-VN", "vi-VN"),
        ("en-US", "en-US"),
        ("pt-BR", "pt-BR"),
        ("en_US", "en-US"),
        (None, None),
    ],
)
def test_legacy_profile_language_is_normalized(value, code):
    profile = EpisodeProfile(
        name="demo", default_briefing="Research overview", language=value
    )
    assert profile.language == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,expected", [("Vietnamese", "Vietnamese"), ("English", "English")]
)
async def test_actual_podcast_creator_accepts_normalized_language(
    label, expected, tmp_path
):
    from podcast_creator.graph import create_podcast

    invoke = AsyncMock(
        return_value={
            "outline": None,
            "transcript": [],
            "audio_clips": [],
            "final_output_file_path": None,
        }
    )
    with (
        patch("podcast_creator.graph.graph.ainvoke", invoke),
        patch("podcast_creator.graph.load_speaker_config", return_value={}),
    ):
        await create_podcast(
            content="Research notes",
            briefing="A clear explanation",
            episode_name="Language regression",
            output_dir=str(tmp_path),
            speaker_config="test",
            language=normalize_podcast_language(label),
        )
    assert invoke.await_args.args[0]["language"] == expected
