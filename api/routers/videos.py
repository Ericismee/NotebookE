import asyncio
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from api.video_service import (
    VideoGenerationRequest,
    VideoGenerationResponse,
    VideoService,
)
from notebooke.config import PODCASTS_FOLDER
from notebooke.exceptions import OpenNotebookError
from notebooke.podcasts.audio_paths import to_relative_audio_path
from notebooke.podcasts.models import PodcastEpisode
from notebooke.videos.models import VideoEpisode
from notebooke.videos.templates import list_video_templates
from notebooke.videos.video_paths import resolve_contained_video_path

router = APIRouter()


@router.get("/videos/templates")
async def get_video_templates():
    """Return the visual presets available to the generation UI."""
    return {"templates": list_video_templates(), "default": "auto"}


class VideoEpisodeResponse(BaseModel):
    id: str
    name: str
    notebook_id: Optional[str] = None
    prompt: str
    video_file: Optional[str] = None
    video_url: Optional[str] = None
    created: Optional[str] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    command_id: Optional[str] = None

@router.post("/videos/generate", response_model=VideoGenerationResponse)
async def generate_video(request: VideoGenerationRequest):
    """
    Generate a video episode.
    Returns immediately with job ID for status tracking.
    """
    try:
        job_id = await VideoService.submit_generation_job(
            name=request.name,
            notebook_id=request.notebook_id,
            content=request.content,
            n_scenes=request.n_scenes,
            duration_minutes=request.duration_minutes,
            image_workflow=request.image_workflow,
            visual_mode=request.visual_mode,
            tts_voice=request.tts_voice,
            voice_model=request.voice_model,
            language=request.language,
            video_type=request.video_type,
            style=request.style,
            character_id=request.character_id,
            custom_prompt=request.custom_prompt,
            aspect_ratio=request.aspect_ratio,
        )

        return VideoGenerationResponse(
            job_id=job_id,
            status="submitted",
            message=f"Video generation started for '{request.name}'",
            name=request.name,
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error generating video: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to generate video"
        )

@router.get("/videos/jobs/{job_id}")
async def get_video_job_status(job_id: str):
    """Get the status of a video generation job"""
    try:
        return await VideoService.get_job_status(job_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching video job status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch job status")

@router.get("/videos/episodes", response_model=List[VideoEpisodeResponse])
async def list_video_episodes():
    """List all video episodes"""
    try:
        episodes = await VideoService.list_episodes()

        try:
            details_by_command = await VideoEpisode.get_job_details_for_commands(
                [episode.command for episode in episodes if episode.command]
            )
        except Exception as e:
            logger.warning(f"Error batch-fetching video job statuses: {str(e)}")
            details_by_command = {}

        response_episodes = []
        for episode in episodes:
            if not episode.command and not episode.video_file:
                continue

            job_status = None
            error_message = None
            if episode.command:
                detail = details_by_command.get(str(episode.command))
                if detail is not None:
                    job_status = detail.get("status", "unknown")
                    error_message = detail.get("error_message")
                    
                    if job_status == "completed" and (detail.get("success") is False or error_message):
                        job_status = "failed"
                else:
                    job_status = "unknown"
            else:
                job_status = "completed"

            video_path = resolve_contained_video_path(episode.video_file)

            video_url = None
            if video_path is not None and video_path.exists():
                video_url = f"/api/videos/episodes/{episode.id}/video"

            response_episodes.append(
                VideoEpisodeResponse(
                    id=str(episode.id),
                    name=episode.name,
                    notebook_id=episode.notebook_id,
                    prompt=episode.prompt,
                    video_file=episode.video_file,
                    video_url=video_url,
                    created=str(episode.created) if episode.created else None,
                    job_status=job_status,
                    error_message=error_message,
                    command_id=str(episode.command) if episode.command else None,
                )
            )

        return response_episodes

    except Exception as e:
        logger.error(f"Error listing video episodes: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list video episodes")

@router.get("/videos/episodes/{episode_id}/video")
async def stream_video_episode(episode_id: str):
    """Stream the video file associated with an episode"""
    try:
        episode = await VideoService.get_episode(episode_id)
    except Exception as e:
        logger.error(f"Error fetching video episode: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.video_file:
        raise HTTPException(status_code=404, detail="Episode has no video file")

    video_path = resolve_contained_video_path(episode.video_file)
    if video_path is None:
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=video_path.name,
    )

@router.delete("/videos/episodes/{episode_id}")
async def delete_video_episode(episode_id: str):
    """Delete a video episode and its associated video file"""
    try:
        episode = await VideoService.get_episode(episode_id)

        if episode.video_file:
            video_path = resolve_contained_video_path(episode.video_file)
            if video_path is None:
                logger.warning(
                    f"Refusing to delete invalid video path for {episode_id}: "
                    f"{episode.video_file}"
                )
            elif video_path.exists():
                try:
                    video_path.unlink()
                    logger.info(f"Deleted video file: {video_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete video file {video_path}: {e}")

        await episode.delete()
        logger.info(f"Deleted video episode: {episode_id}")
        return {"message": "Episode deleted successfully", "episode_id": episode_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting video episode: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to delete video episode"
        )

@router.post("/videos/episodes/{episode_id}/to-podcast")
async def extract_video_to_podcast(episode_id: str):
    """Extract audio from a video episode and create a PodcastEpisode"""
    try:
        episode = await VideoService.get_episode(episode_id)
    except Exception as e:
        logger.error(f"Error fetching video episode: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.video_file:
        raise HTTPException(status_code=400, detail="Episode has no video file yet")

    video_path = resolve_contained_video_path(episode.video_file)
    if video_path is None:
        raise HTTPException(status_code=403, detail="Access to file denied")
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    # Generate output directory for podcast
    episode_dir_name = str(uuid.uuid4())
    output_dir = Path(PODCASTS_FOLDER) / "episodes" / episode_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    audio_filename = f"combined_{uuid.uuid4().hex}.mp3"
    audio_path = output_dir / audio_filename

    # Extract audio using moviepy
    try:
        from moviepy import VideoFileClip
        
        def extract_audio(vid_path: str, aud_path: str) -> None:
            clip = VideoFileClip(vid_path)
            try:
                if clip.audio is None:
                    raise ValueError("Video does not have an audio track")
                clip.audio.write_audiofile(aud_path, codec="mp3", logger=None)
            finally:
                clip.close()

        await asyncio.to_thread(extract_audio, str(video_path), str(audio_path))
    except Exception as e:
        logger.error(f"Audio extraction failed: {str(e)}")
        audio_path.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise HTTPException(
            status_code=500, detail="Failed to extract audio from video"
        ) from e

    # Create PodcastEpisode
    audio_file_rel = to_relative_audio_path(str(audio_path))
    
    # Use empty dicts for profiles to satisfy any structural requirements,
    # since this is an auto-generated podcast from video
    podcast = PodcastEpisode(
        name=f"{episode.name} (Audio)",
        notebook_id=episode.notebook_id,
        episode_profile={"name": "Video Audio", "outline_llm": None, "transcript_llm": None},
        speaker_profile={"name": "Video Speaker", "tts_provider": "edgetts", "tts_model": "edge-tts", "speakers": []},
        audio_file=audio_file_rel,
        briefing=episode.prompt,
        content="Generated from Video",
    )
    await podcast.save()

    return {
        "message": "Podcast created successfully",
        "podcast_id": str(podcast.id),
        "audio_file": audio_file_rel
    }
