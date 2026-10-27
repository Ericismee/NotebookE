"""
Async surreal-commands worker command for Pixelle-Video generation.

Pattern mirrors podcast_commands.py exactly:
  - Input/Output are CommandInput/CommandOutput subclasses
  - @command decorator with retry={"max_attempts": 1}
  - Creates the VideoEpisode DB record before calling Pixelle-Video
  - Saves the output video path relative to VIDEOS_FOLDER
"""



import os
import time
from pathlib import Path
from typing import Literal, Optional

from loguru import logger
from pydantic import Field
from surreal_commands import CommandInput, CommandOutput, command

from notebooke.database.repository import ensure_record_id
from notebooke.videos.models import VideoEpisode
from notebooke.videos.pixelle_bridge import VIDEOS_FOLDER
from notebooke.videos.storytelling import decode_scene_plan
from notebooke.videos.templates import select_video_template
from notebooke.videos.video_paths import to_relative_video_path

# ---------------------------------------------------------------------------
# Input / Output schemas
# ---------------------------------------------------------------------------

class VideoGenerationInput(CommandInput):
    name: str                               # Episode name
    content: str                            # Full text content from notebook
    notebook_id: Optional[str] = None      # Source notebook (for display)
    n_scenes: int = Field(default=5, ge=3, le=16)
    duration_minutes: Literal[1, 3, 5] = 3
    image_workflow: Optional[str] = None
    visual_mode: str = "auto"
    tts_voice: str = "vi-VN-HoaiMyNeural"
    voice_model: Optional[str] = None
    language: Optional[str] = None
    video_type: str = "summary"
    style: str = "auto"
    character_id: str = "AI"
    custom_prompt: Optional[str] = None
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16)$")


class VideoGenerationOutput(CommandOutput):
    success: bool
    episode_id: Optional[str] = None
    video_file_path: Optional[str] = None  # relative to VIDEOS_FOLDER
    duration: Optional[float] = None
    file_size: Optional[int] = None
    processing_time: float
    error_message: Optional[str] = None

VideoGenerationInput.model_rebuild()
VideoGenerationOutput.model_rebuild()


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@command("generate_video", app="notebooke", retry={"max_attempts": 1})
async def generate_video_command(
    input_data: VideoGenerationInput,
) -> VideoGenerationOutput:
    """
    Generate a short video from notebook content using Pixelle-Video.

    Uses an AI-directed storyboard, scene-specific visual composition,
    progressive captions, and NotebookE's configured default chat model.
    """
    start_time = time.time()

    template = select_video_template(input_data.style, input_data.content)

    logger.info(
        f"[video-cmd] Starting video generation: {input_data.name} with "
        f"{input_data.n_scenes} scenes, template={template.id}"
    )

    # ------------------------------------------------------------------
    # 1. Create the VideoEpisode record immediately so it's visible in UI
    # ------------------------------------------------------------------
    episode = VideoEpisode(
        name=input_data.name,
        prompt=input_data.content[:500],      # store a snippet only
        notebook_id=input_data.notebook_id,
        n_scenes=input_data.n_scenes,
        image_workflow=input_data.image_workflow,
        visual_mode=input_data.visual_mode,
        tts_voice=input_data.tts_voice,
        voice_model=input_data.voice_model,
        language=input_data.language,
        video_type=input_data.video_type,
        style=template.id,
        character_id=input_data.character_id,
        custom_prompt=input_data.custom_prompt,
        aspect_ratio=input_data.aspect_ratio,
        command=ensure_record_id(input_data.execution_context.command_id)
        if input_data.execution_context
        else None,
    )
    await episode.save()
    logger.info(f"[video-cmd] VideoEpisode record created: {episode.id}")

    core = None
    try:
        # ------------------------------------------------------------------
        # 2. Build Pixelle-Video core with NotebookE credentials
        # ------------------------------------------------------------------
        from notebooke.videos.pixelle_bridge import build_pixelle_core

        core, llm_config = await build_pixelle_core(
            language=input_data.language or "Vietnamese",
            template_preset=template.id,
            voice_model=input_data.voice_model,
        )

        # Store the model name on the episode
        episode.llm_model = llm_config.get("model")
        await episode.save()

        # ------------------------------------------------------------------
        # 3. Pass a concise production brief. The bridge owns the detailed
        # director rules, which keeps instructions out of scene metadata and
        # avoids sending the same long prompt twice to the model.
        # ------------------------------------------------------------------
        character = input_data.character_id or "AI tự chọn"
        custom_instruction = (input_data.custom_prompt or "").strip() or "Không có"
        director_prompt = f"""VIDEO BRIEF
Loại video: {input_data.video_type}
Ngôn ngữ: {input_data.language or 'Vietnamese'}
Mẫu trình bày: {template.name} ({template.tone})
Định hướng mỹ thuật: {template.direction}
Nhân vật: {character}
Định hướng hình ảnh: {input_data.visual_mode}
Yêu cầu riêng: {custom_instruction}

SOURCE CONTENT
{input_data.content}
"""

        logger.info(
            f"[video-cmd] Calling pixelle_video.generate_video "
            f"(n_scenes={input_data.n_scenes}, model={llm_config.get('model')})"
        )

        # Determine resolution path prefix based on aspect ratio
        res_prefix = "1920x1080" if input_data.aspect_ratio == "16:9" else "1080x1920"
        
        # One visual system, with layout and composition chosen independently
        # for every scene by the storyboard director.
        template_name = "image_notebooke_adaptive.html"

        # Clean title: remove giant raw names like 'arXiv - Chuyên mục Cryptography and Security - Video'
        raw_name = input_data.name.replace(" - Video", "").replace(" Video", "").strip()
        if " - Chuyên mục " in raw_name:
            clean_title = raw_name.split(" - Chuyên mục ")[-1].strip()
        elif " - " in raw_name:
            clean_title = raw_name.split(" - ")[-1].strip()
        else:
            clean_title = raw_name
        clean_title = clean_title[:30].strip()

        if episode.id is None:
            raise RuntimeError("Video episode record was created without an ID")

        result = await core.generate_video(
            text=director_prompt,
            title=clean_title,
            pipeline="standard",
            mode="generate",
            n_scenes=input_data.n_scenes,
            duration_minutes=input_data.duration_minutes,
            min_narration_words=max(40, min(55, int(input_data.duration_minutes * 150 / input_data.n_scenes) - 10)),
            max_narration_words=min(90, int(input_data.duration_minutes * 150 / input_data.n_scenes) + 10),
            tts_inference_mode="local",
            voice_id=input_data.tts_voice,
            tts_voice=input_data.tts_voice,
            media_workflow=input_data.image_workflow or os.getenv(
                "OPEN_NOTEBOOK_VIDEO_IMAGE_WORKFLOW",
                "api/openai/gpt-image-2.5-flare-2026-09-08",
            ),
            frame_template=f"{res_prefix}/{template_name}",
            output_path=str(
                Path(VIDEOS_FOLDER) / f"{episode.id.split(':')[-1]}.mp4"
            ),
        )

        # ------------------------------------------------------------------
        # 4. Compute relative path for storage (mirrors podcast audio_paths)
        # ------------------------------------------------------------------
        abs_video = Path(result.video_path).resolve()
        if not abs_video.is_file() or abs_video.stat().st_size == 0:
            raise RuntimeError("Video renderer did not create a valid MP4 file")
        rel_path = to_relative_video_path(abs_video)

        # Keep the full direction so the UI can expose a useful storyboard later.
        storyboard_snapshot = None
        try:
            sb = result.storyboard
            scenes = [
                decode_scene_plan(frame.narration, index)
                for index, frame in enumerate(sb.frames)
            ]
            storyboard_snapshot = {
                "title": sb.title,
                "template": template.id,
                "template_name": template.name,
                "n_frames": len(sb.frames),
                "narrations": [scene.narration for scene in scenes],
                "scenes": [
                    {
                        "title": scene.title,
                        "purpose": scene.purpose,
                        "layout": scene.layout,
                        "visual_prompt": scene.visual_prompt,
                        "visual_keywords": scene.visual_keywords,
                        "narration": scene.narration,
                    }
                    for scene in scenes
                ],
            }
        except Exception:
            pass

        # ------------------------------------------------------------------
        # 5. Update DB record with output
        # ------------------------------------------------------------------
        episode.video_file = rel_path
        episode.duration = result.duration
        episode.file_size = result.file_size
        episode.storyboard = storyboard_snapshot
        await episode.save()

        processing_time = time.time() - start_time
        logger.info(
            f"[video-cmd] Done in {processing_time:.1f}s — "
            f"{result.duration:.1f}s video, {result.file_size // 1024} KB"
        )

        return VideoGenerationOutput(
            success=True,
            episode_id=str(episode.id),
            video_file_path=rel_path,
            duration=result.duration,
            file_size=result.file_size,
            processing_time=processing_time,
        )

    except Exception as exc:
        processing_time = time.time() - start_time
        logger.error(f"[video-cmd] Generation failed: {exc}")
        logger.exception(exc)

        # Persist error on the episode record
        try:
            episode.error_message = str(exc)[:1000]
            await episode.save()
        except Exception:
            pass

        return VideoGenerationOutput(
            success=False,
            episode_id=str(episode.id) if episode.id else None,
            processing_time=processing_time,
            error_message=str(exc)[:1000],
        )
    finally:
        if core is not None:
            try:
                await core.cleanup()
            except Exception as cleanup_error:
                logger.warning(f"[video-cmd] Core cleanup failed: {cleanup_error}")
