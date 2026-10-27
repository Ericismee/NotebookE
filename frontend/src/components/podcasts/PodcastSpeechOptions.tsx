'use client'

import { useEffect, useId, useRef } from 'react'
import { useSpeakerProfiles } from '@/lib/hooks/use-podcasts'
import { useModels } from '@/lib/hooks/use-models'
import { useTranslation } from '@/lib/hooks/use-translation'
import { languageModel, speechLanguage } from '@/lib/tts-voices'
import { ModelSelector } from '@/components/common/ModelSelector'
import { VoiceSelector } from '@/components/common/VoiceSelector'

export interface PodcastSpeechSelection { modelId: string; voices: string[] }

export function PodcastSpeechOptions({ profileId, language, value, onChange }: {
  profileId?: string | null
  language: string
  value: PodcastSpeechSelection
  onChange: (value: PodcastSpeechSelection) => void
}) {
  const { t } = useTranslation()
  const { speakerProfiles } = useSpeakerProfiles()
  const { data: models = [] } = useModels()
  const id = useId()
  const initialized = useRef('')
  const profile = speakerProfiles.find(item => item.id === profileId || item.name === profileId)
  const lang = speechLanguage(language)
  useEffect(() => {
    if (!profile) { initialized.current = ''; return }
    if (!models.length) return
    const key = `${profile.id}:${lang}`
    if (initialized.current === key) return
    initialized.current = key
    const modelId = languageModel(models, lang, profile.voice_model || '')
    const englishVoices = ['en-US-GuyNeural', 'en-US-JennyNeural', 'en-US-AndrewMultilingualNeural', 'en-US-AvaMultilingualNeural']
    const vieneu = models.find(model => model.id === modelId)?.name.toLowerCase().startsWith('vieneu')
    onChange({ modelId, voices: profile.speakers.map((speaker, index) => lang === 'en' ? englishVoices[index % englishVoices.length] : vieneu && (speaker.voice_id.startsWith('en-') || speaker.voice_id.startsWith('vi-')) ? (index % 2 ? 'Mai Anh' : 'Hải Đăng') : speaker.voice_id) })
  }, [profile, models, lang, onChange])

  if (!profile) return null
  return <div className="space-y-3">
    <ModelSelector label={t('podcasts.voiceModel')} modelType="text_to_speech" value={value.modelId}
      onChange={modelId => onChange({ modelId, voices: profile.speakers.map((_, index) => lang === 'en' ? (index % 2 ? 'en-US-JennyNeural' : 'en-US-GuyNeural') : models.find(model => model.id === modelId)?.name.toLowerCase().startsWith('vieneu') ? (index % 2 ? 'Mai Anh' : 'Hải Đăng') : (index % 2 ? 'vi-VN-HoaiMyNeural' : 'vi-VN-NamMinhNeural')) })} />
    {profile.speakers.map((speaker, index) => <div key={index} className="space-y-2">
      <label htmlFor={`${id}-${index}`} className="text-sm font-medium">{speaker.name}</label>
      <VoiceSelector id={`${id}-${index}`} modelId={value.modelId} language={lang} value={value.voices[index] || ''}
        onModelChange={modelId => onChange({ ...value, modelId })}
        onChange={voice => onChange({ ...value, voices: profile.speakers.map((_, i) => i === index ? voice : value.voices[i] || '') })} />
    </div>)}
  </div>
}
