'use client'

import { useState, useEffect, useMemo, useRef } from 'react'
import {
  IconHeadphones,
  IconVideo,
  IconLoader2,
  IconPlayerPlay,
} from '@tabler/icons-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useGenerateVideo, useVideoJobStatus, useVideoTemplates } from '@/lib/hooks/use-videos'
import { normalizeVideoTemplateId, resolveVideoAssetUrl, videosApi } from '@/lib/api/videos'
import { useGeneratePodcast, useEpisodeProfiles, usePodcastJobStatus } from '@/lib/hooks/use-podcasts'
import { podcastsApi, resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { useToast } from '@/lib/hooks/use-toast'
import { useSettings } from '@/lib/hooks/use-settings'
import { useModels, useModelDefaults } from '@/lib/hooks/use-models'
import { ModelSelector } from '@/components/common/ModelSelector'
import { VoiceSelector } from '@/components/common/VoiceSelector'
import { PodcastSpeechOptions, PodcastSpeechSelection } from '@/components/podcasts/PodcastSpeechOptions'
import { languageModel } from '@/lib/tts-voices'
import { useTranslation } from '@/lib/hooks/use-translation'

interface NotebookMediaQuickActionsProps {
  notebookId: string
  notebookName: string
}

export function NotebookMediaQuickActions({ notebookId, notebookName }: NotebookMediaQuickActionsProps) {
  const { toast } = useToast()
  const { t } = useTranslation()

  const [activeVideoJobId, setActiveVideoJobId] = useState<string | null>(null)
  const [activePodcastJobId, setActivePodcastJobId] = useState<string | null>(null)

  const [showVideoDialog, setShowVideoDialog] = useState(false)
  const [showPodcastDialog, setShowPodcastDialog] = useState(false)
  const [showCreateVideoDialog, setShowCreateVideoDialog] = useState(false)
  const [showCreatePodcastDialog, setShowCreatePodcastDialog] = useState(false)

  // Form State
  const [videoLanguage, setVideoLanguage] = useState('Tiếng Việt')
  const [podcastLanguage, setPodcastLanguage] = useState('Tiếng Việt')
  const [podcastSpeech, setPodcastSpeech] = useState<PodcastSpeechSelection>({ modelId: '', voices: [] })
  const [episodeProfileName, setEpisodeProfileName] = useState('')
  const [videoType, setVideoType] = useState('Tạo video tóm tắt')
  const [videoDuration, setVideoDuration] = useState('3')
  const [videoStyle, setVideoStyle] = useState('auto')
  const [videoCharacter, setVideoCharacter] = useState('AI tự chọn')
  const [videoVoice, setVideoVoice] = useState('vi-VN-HoaiMyNeural')
  const [videoVoiceModel, setVideoVoiceModel] = useState('')
  const [videoAspectRatio, setVideoAspectRatio] = useState('16:9') // Ngang
  const [imageWorkflow, setImageWorkflow] = useState('')
  const [visualMode, setVisualMode] = useState('auto')
  const [customInstruction, setCustomInstruction] = useState('')
  
  const { data: settings } = useSettings()
  const { data: videoTemplatesData } = useVideoTemplates()
  const videoTemplates = videoTemplatesData?.templates ?? []
  const { data: models = [], isLoading: modelsLoading } = useModels()
  const { data: modelDefaults } = useModelDefaults()
  const voicePreferenceInitialized = useRef(false)
  const preferredVieNeuApplied = useRef(false)
  useEffect(() => {
    if (voicePreferenceInitialized.current || !modelDefaults || modelsLoading) return
    voicePreferenceInitialized.current = true
    const preferred = models.find(model => model.id === modelDefaults?.default_text_to_speech_model)
    if (preferred?.name.toLowerCase().startsWith('vieneu')) {
      preferredVieNeuApplied.current = true
      setVideoVoiceModel(preferred.id)
      setVideoVoice('Hải Đăng')
    }
  }, [modelDefaults, models, modelsLoading])
  const imageModels = useMemo(
    () => models.filter(model =>
      ['openai', 'openai_compatible', 'openai-compatible'].includes(model.provider.toLowerCase()) &&
      /(?:gpt-image|dall-e|image-generation|image-gen)/i.test(model.name)
    ),
    [models]
  )

  useEffect(() => {
    if (settings) {
      if (settings.default_video_duration) setVideoDuration(settings.default_video_duration)
      if (settings.default_video_style) setVideoStyle(normalizeVideoTemplateId(settings.default_video_style))
      if (settings.default_video_character) setVideoCharacter(settings.default_video_character)
      if (settings.default_video_voice && !preferredVieNeuApplied.current) setVideoVoice(settings.default_video_voice)
      if (settings.default_video_aspect_ratio) setVideoAspectRatio(settings.default_video_aspect_ratio)
    }
  }, [settings])

  useEffect(() => {
    if (imageModels.length === 0) return
    const available = imageModels.some(model => `api/openai/${model.name}` === imageWorkflow)
    if (!available) setImageWorkflow(`api/openai/${imageModels[0].name}`)
  }, [imageModels, imageWorkflow])
  
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)

  const generateVideo = useGenerateVideo()
  const generatePodcast = useGeneratePodcast()
  const podcastSubmitLockRef = useRef(false)
  const videoSubmitLockRef = useRef(false)
  
  const { data: epProfiles } = useEpisodeProfiles()

  const videoStatus = useVideoJobStatus(activeVideoJobId)
  const podcastStatus = usePodcastJobStatus(activePodcastJobId)

  // Fetch existing media on mount
  useEffect(() => {
    let mounted = true
    const fetchExistingMedia = async () => {
      try {
        const [videos, podcasts] = await Promise.all([
          videosApi.listEpisodes().catch(() => []),
          podcastsApi.listEpisodes().catch(() => [])
        ])
        
        if (!mounted) return

        // Find latest video for this notebook
        const notebookVideos = videos.filter(v => v.notebook_id === notebookId && v.job_status === 'completed' && v.video_url)
        if (notebookVideos.length > 0) {
          // Sort by created descending
          notebookVideos.sort((a, b) => new Date(b.created || 0).getTime() - new Date(a.created || 0).getTime())
          const url = await resolveVideoAssetUrl(notebookVideos[0].video_url)
          if (mounted) setVideoUrl(url || null)
        }

        // Find latest podcast for this notebook
        const notebookPodcasts = podcasts.filter(
          p => p.notebook_id === notebookId && p.audio_file
        )
        if (notebookPodcasts.length > 0) {
          notebookPodcasts.sort((a, b) => new Date(b.created || 0).getTime() - new Date(a.created || 0).getTime())
          const url = await resolvePodcastAssetUrl(notebookPodcasts[0].audio_file)
          if (mounted) setAudioUrl(url || null)
        }
      } catch (e) {
        console.error('Error fetching existing media', e)
      }
    }
    fetchExistingMedia()
    return () => { mounted = false }
  }, [notebookId, notebookName])

  // Handle video completion
  useEffect(() => {
    if (!activeVideoJobId) return
    if (videoStatus.data?.status === 'completed') {
      const fetchUrl = async () => {
        try {
          const episodes = await videosApi.listEpisodes()
          const matched = episodes.find(e => e.command_id === activeVideoJobId)
          if (matched?.video_url) {
            const url = await resolveVideoAssetUrl(matched.video_url)
            setVideoUrl(url || null)
          }
        } catch (e) {
          console.error('Could not fetch video episode url', e)
        }
      }
      fetchUrl()
      setActiveVideoJobId(null)
      toast({
        title: 'Thành công',
        description: 'Tạo Video thành công!',
      })
    } else if (
      videoStatus.data?.status === 'failed' ||
      videoStatus.data?.status === 'error' ||
      videoStatus.data?.status === 'cancelled'
    ) {
      setActiveVideoJobId(null)
      toast({
        title: 'Lỗi',
        description: 'Tạo Video thất bại: ' + videoStatus.data?.error_message,
        variant: 'destructive',
      })
    }
  }, [
    activeVideoJobId,
    toast,
    videoStatus.data?.error_message,
    videoStatus.data?.status,
  ])

  // Handle podcast completion
  useEffect(() => {
    if (!activePodcastJobId) return
    if (podcastStatus.data?.status === 'completed') {
      const fetchUrl = async () => {
        try {
          const episodes = await podcastsApi.listEpisodes()
          const matched = episodes.find(e => e.command_id === activePodcastJobId)
          if (matched && matched.audio_file) {
            const url = await resolvePodcastAssetUrl(matched.audio_file)
            setAudioUrl(url || null)
          }
        } catch (e) {
          console.error('Could not fetch podcast episode url', e)
        }
      }
      fetchUrl()
      setActivePodcastJobId(null)
      toast({
        title: 'Thành công',
        description: 'Tạo Podcast thành công!',
      })
    } else if (
      podcastStatus.data?.status === 'failed' ||
      podcastStatus.data?.status === 'error' ||
      podcastStatus.data?.status === 'cancelled'
    ) {
      setActivePodcastJobId(null)
      toast({
        title: 'Lỗi',
        description: 'Tạo Podcast thất bại: ' + podcastStatus.data?.error_message,
        variant: 'destructive',
      })
    }
  }, [
    activePodcastJobId,
    podcastStatus.data?.error_message,
    podcastStatus.data?.status,
    toast,
  ])

  const handleGeneratePodcast = () => {
    const availableProfile = epProfiles?.find(
      profile => profile.speaker_config && profile.speaker_config_name
    )
    if (!availableProfile) {
      toast({
        title: 'Thiếu cấu hình',
        description: 'Vui lòng cấu hình đầy đủ Episode Profile, Speaker Profile và các model trước.',
        variant: 'destructive',
      })
      return
    }
    if (!epProfiles?.some(profile => profile.name === episodeProfileName)) {
      setEpisodeProfileName(availableProfile.name)
    }
    setShowCreatePodcastDialog(true)
  }

  const handleGeneratePodcastSubmit = async () => {
    if (podcastSubmitLockRef.current) return

    const selectedProfile = epProfiles?.find(
      profile => profile.name === episodeProfileName
    )
    if (!selectedProfile?.speaker_config || !selectedProfile.speaker_config_name) {
      toast({
        title: 'Thiếu cấu hình',
        description: 'Episode Profile đã chọn không còn Speaker Profile hợp lệ.',
        variant: 'destructive',
      })
      return
    }

    podcastSubmitLockRef.current = true
    try {
      const res = await generatePodcast.mutateAsync({
        episode_profile: selectedProfile.name,
        speaker_profile: selectedProfile.speaker_config,
        episode_name: `${notebookName} - Podcast`,
        notebook_id: notebookId,
        briefing_suffix: `[LANGUAGE REQUIREMENT]: Create the entire podcast script and dialogue strictly in ${podcastLanguage}.`,
        language: podcastLanguage,
        voice_model: podcastSpeech.modelId || undefined,
        speaker_voices: podcastSpeech.voices.length ? podcastSpeech.voices : undefined,
      })
      setShowCreatePodcastDialog(false)
      setActivePodcastJobId(res.job_id)
      setAudioUrl(null)
      toast({ title: 'Đang tạo', description: 'Bắt đầu tạo Podcast trong nền...' })
    } catch (e) {
      toast({ title: 'Lỗi', description: (e as Error).message, variant: 'destructive' })
    } finally {
      podcastSubmitLockRef.current = false
    }
  }

  const handleGenerateVideo = async () => {
    if (videoSubmitLockRef.current) return
    videoSubmitLockRef.current = true
    try {
      const durationMinutes = Number(videoDuration) as 1 | 3 | 5
      const sceneCount = durationMinutes === 1 ? 3 : durationMinutes === 3 ? 6 : 10
      const res = await generateVideo.mutateAsync({
        name: `${notebookName} - Video`,
        notebook_id: notebookId,
        n_scenes: sceneCount,
        duration_minutes: durationMinutes,
        tts_voice: videoVoice,
        voice_model: videoVoiceModel || undefined,
        language: videoLanguage,
        video_type: videoType,
        style: videoStyle,
        character_id: videoCharacter,
        image_workflow: imageWorkflow,
        visual_mode: visualMode,
        custom_prompt: customInstruction,
        aspect_ratio: videoAspectRatio
      })
      setShowCreateVideoDialog(false)
      setActiveVideoJobId(res.job_id)
      setVideoUrl(null)
      toast({ title: 'Đang tạo', description: 'Bắt đầu tạo Video trong nền...' })
    } catch (e) {
      toast({ title: 'Lỗi', description: (e as Error).message, variant: 'destructive' })
    } finally {
      videoSubmitLockRef.current = false
    }
  }

  const handleVideoLanguageChange = (language: string) => {
    setVideoLanguage(language)
    setVideoVoiceModel(languageModel(models, language, videoVoiceModel))
    setVideoVoice(language === 'English' ? 'en-US-JennyNeural' : 'Hải Đăng')
  }

  const isPodcastLoading = activePodcastJobId !== null || generatePodcast.isPending
  const isVideoLoading = activeVideoJobId !== null || generateVideo.isPending

  return (
    <div className="col-span-2 flex min-w-0 gap-2 sm:col-span-1">
      {audioUrl ? (
        <Button className="min-w-0 flex-1 justify-center sm:flex-none" variant="default" size="sm" onClick={() => setShowPodcastDialog(true)}>
          <IconPlayerPlay className="h-4 w-4 mr-2" /> Nghe Podcast
        </Button>
      ) : (
        <Button className="min-w-0 flex-1 justify-center sm:flex-none" variant="outline" size="sm" onClick={handleGeneratePodcast} disabled={isPodcastLoading}>
          {isPodcastLoading ? <IconLoader2 className="h-4 w-4 mr-2 animate-spin" /> : <IconHeadphones className="h-4 w-4 mr-2" />}
          Tạo Podcast
        </Button>
      )}
      
      {videoUrl ? (
        <Button className="min-w-0 flex-1 justify-center sm:flex-none" variant="default" size="sm" onClick={() => setShowVideoDialog(true)}>
          <IconPlayerPlay className="h-4 w-4 mr-2" /> Xem Video
        </Button>
      ) : (
        <Button className="min-w-0 flex-1 justify-center sm:flex-none" variant="outline" size="sm" onClick={() => setShowCreateVideoDialog(true)} disabled={isVideoLoading}>
          {isVideoLoading ? <IconLoader2 className="h-4 w-4 mr-2 animate-spin" /> : <IconVideo className="h-4 w-4 mr-2" />}
          Tạo Video
        </Button>
      )}

      {/* Podcast Dialog */}
      <Dialog open={showPodcastDialog} onOpenChange={setShowPodcastDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nghe Podcast</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center justify-center p-4">
            {audioUrl && (
              <audio controls autoPlay className="w-full">
                <source src={audioUrl} type="audio/mpeg" />
                Your browser does not support the audio element.
              </audio>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Create Podcast Dialog */}
      <Dialog open={showCreatePodcastDialog} onOpenChange={setShowCreatePodcastDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Tạo AI Podcast</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-6 py-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium">Episode Profile</label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={episodeProfileName}
                onChange={event => setEpisodeProfileName(event.target.value)}
              >
                {epProfiles
                  ?.filter(profile => profile.speaker_config && profile.speaker_config_name)
                  .map(profile => (
                    <option key={profile.id} value={profile.name}>
                      {profile.name} · {profile.speaker_config_name}
                    </option>
                  ))}
              </select>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">Ngôn ngữ Podcast (Language)</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={podcastLanguage} onChange={e => setPodcastLanguage(e.target.value)}>
                <option value="Tiếng Việt">Tiếng Việt</option>
                <option value="English">English</option>
              </select>
            </div>
            <PodcastSpeechOptions profileId={epProfiles?.find(profile => profile.name === episodeProfileName)?.speaker_config}
              language={podcastLanguage} value={podcastSpeech} onChange={setPodcastSpeech} />
            <Button onClick={handleGeneratePodcastSubmit} disabled={generatePodcast.isPending} className="w-full mt-2">
              {generatePodcast.isPending ? (
                <IconLoader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <IconHeadphones className="w-4 h-4 mr-2" />
              )}
              {generatePodcast.isPending ? 'Đang gửi...' : 'Bắt đầu tạo Podcast'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Create Video Dialog */}
      <Dialog open={showCreateVideoDialog} onOpenChange={setShowCreateVideoDialog}>
        <DialogContent variant="drawer" className="max-h-screen">
          <DialogHeader className="shrink-0 border-b px-6 py-5 pr-14">
            <DialogTitle className="text-xl">Tạo video bằng AI</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 space-y-5 overflow-y-auto px-6 pb-6 pt-1">
            <div className="rounded-xl border border-primary/20 bg-primary/[0.04] p-4">
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-primary/10 p-2 text-primary"><IconVideo className="h-5 w-5" /></div>
                <div>
                  <p className="text-sm font-semibold">AI Visual Director</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">AI sẽ phân tích nội dung, chia thành các cảnh, tạo hình ảnh theo chủ đề, đọc lời thuyết minh và đồng bộ chữ theo giọng nói.</p>
                </div>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <label className="text-sm font-medium">Ngôn ngữ Video (Language)</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={videoLanguage} onChange={e => handleVideoLanguageChange(e.target.value)}>
                <option value="Tiếng Việt">Tiếng Việt</option>
                <option value="English">English</option>
              </select>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">1. Video Type</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={videoType} onChange={e => setVideoType(e.target.value)}>
                <option value="Tạo video tóm tắt">Tạo video tóm tắt</option>
                <option value="Tạo video giải thích">Tạo video giải thích</option>
                <option value="Tạo video bài giảng">Tạo video bài giảng</option>
                <option value="Tạo video hỏi đáp">Tạo video hỏi đáp</option>
              </select>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">2. Duration (Minutes)</label>
              <div className="grid grid-cols-3 gap-2">
                {['1', '3', '5'].map(d => (
                  <label key={d} className={`flex cursor-pointer flex-col rounded-lg border px-3 py-2 text-center transition-colors ${videoDuration === d ? 'border-primary bg-primary/5 text-primary' : 'border-input hover:bg-muted/60'}`}>
                    <input type="radio" name="duration" value={d} checked={videoDuration === d} onChange={e => setVideoDuration(e.target.value)} />
                    <span className="mt-1 text-sm font-medium">{d} phút</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-3 sm:col-span-2">
              <div>
                <label className="text-sm font-medium">3. {t('common.videoTemplateLabel')}</label>
                <p className="mt-1 text-xs text-muted-foreground">{t('common.videoTemplateHint')}</p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setVideoStyle('auto')}
                  className={`rounded-xl border p-3 text-left transition-colors ${videoStyle === 'auto' ? 'border-primary bg-primary/5 ring-1 ring-primary/20' : 'border-input hover:bg-muted/50'}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold">{t('common.videoTemplateAuto')}</span>
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">{t('common.recommended')}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{t('common.videoTemplateAutoDescription')}</p>
                </button>
                {videoTemplates.map(template => (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => setVideoStyle(template.id)}
                    className={`overflow-hidden rounded-xl border text-left transition-colors ${videoStyle === template.id ? 'border-primary ring-1 ring-primary/20' : 'border-input hover:bg-muted/50'}`}
                  >
                    <div
                      className="flex h-12 items-center justify-between px-3"
                      style={{ backgroundColor: template.background, color: template.ink }}
                    >
                      <span className="text-xs font-bold">Aa</span>
                      <span className="flex gap-1">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: template.accent }} />
                        <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: template.accent_2 }} />
                      </span>
                    </div>
                    <div className="p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">{template.name}</span>
                        <span className="text-[10px] uppercase text-muted-foreground">{template.tone === 'light' ? t('common.light') : t('common.dark')}</span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{template.description}</p>
                    </div>
                  </button>
                ))}
              </div>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">4. Character</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={videoCharacter} onChange={e => setVideoCharacter(e.target.value)}>
                <option value="AI tự chọn">🤖 AI Tự chọn</option>
                <option value="Penguin">🐧 Penguin</option>
                <option value="Cat">🐱 Cat</option>
                <option value="Fox">🦊 Fox</option>
                <option value="Rabbit">🐰 Rabbit</option>
                <option value="Bear">🐻 Bear</option>
                <option value="Robot">🤖 Robot</option>
                <option value="Owl">🦉 Owl</option>
              </select>
            </div>
            </div>
            <div className="grid gap-2">
              <ModelSelector
                label={t('podcasts.voiceModel')}
                modelType="text_to_speech"
                value={videoVoiceModel}
                onChange={value => {
                  setVideoVoiceModel(value)
                  const selected = models.find(model => model.id === value)
                  setVideoVoice(videoLanguage === 'English' ? 'en-US-JennyNeural' : selected?.name.toLowerCase().startsWith('vieneu') ? 'Hải Đăng' : 'vi-VN-HoaiMyNeural')
                }}
              />
              <label htmlFor="video-voice" className="text-sm font-medium">{t('podcasts.voiceId')}</label>
              <VoiceSelector id="video-voice" modelId={videoVoiceModel} language={videoLanguage} value={videoVoice} onChange={setVideoVoice} onModelChange={setVideoVoiceModel} />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">6. Aspect Ratio (Tỷ lệ khung hình)</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={videoAspectRatio} onChange={e => setVideoAspectRatio(e.target.value)}>
                <option value="16:9">Ngang (16:9 - YouTube/Web)</option>
                <option value="9:16">Dọc (9:16 - TikTok/Shorts)</option>
              </select>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">Model tạo hình ảnh</label>
                <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={imageWorkflow} onChange={e => setImageWorkflow(e.target.value)} disabled={modelsLoading || imageModels.length === 0}>
                  {modelsLoading ? <option value="">Đang tải model tạo ảnh...</option> : null}
                  {!modelsLoading && imageModels.length === 0 ? <option value="">Chưa có model tạo ảnh</option> : null}
                  {imageModels.map(model => (
                    <option key={model.id} value={`api/openai/${model.name}`}>{model.name}</option>
                  ))}
                </select>
                <p className="text-[11px] text-muted-foreground">Chỉ hiển thị model OpenAI có tên nhận diện là model tạo ảnh.</p>
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-medium">Cách trình bày hình ảnh</label>
                <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={visualMode} onChange={e => setVisualMode(e.target.value)}>
                  <option value="auto">AI tự chọn theo nội dung</option>
                  <option value="infographic">Ưu tiên infographic và biểu đồ</option>
                  <option value="diagram">Ưu tiên sơ đồ và quy trình</option>
                  <option value="cinematic">Ưu tiên hình ảnh điện ảnh</option>
                  <option value="mixed">Phối hợp đa dạng</option>
                </select>
              </div>
            </div>
            <details className="rounded-lg border bg-muted/20 px-3 py-2">
              <summary className="cursor-pointer text-sm font-medium">Tùy chỉnh nâng cao</summary>
              <div className="grid gap-2 pt-3">
                <label className="text-sm font-medium">7. Additional Instruction</label>
              <textarea 
                className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[80px]" 
                placeholder="Ví dụ: Hãy giải thích cho học sinh lớp 5, dùng ví dụ mua kẹo..."
                value={customInstruction}
                onChange={e => setCustomInstruction(e.target.value)}
              />
              </div>
            </details>
            <Button onClick={handleGenerateVideo} disabled={generateVideo.isPending} className="sticky bottom-0 h-11 w-full shadow-lg">
              {generateVideo.isPending ? (
                <IconLoader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <IconVideo className="w-4 h-4 mr-2" />
              )}
              {generateVideo.isPending ? 'Đang gửi...' : 'Tạo Video'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Video Dialog */}
      <Dialog open={showVideoDialog} onOpenChange={setShowVideoDialog}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Xem Video</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center justify-center bg-black rounded overflow-hidden">
            {videoUrl && (
              <video controls autoPlay className="w-full max-h-[70vh]">
                <source src={videoUrl} type="video/mp4" />
                Your browser does not support the video tag.
              </video>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
