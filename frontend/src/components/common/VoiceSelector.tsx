'use client'

import { Input } from '@/components/ui/input'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { modelsApi } from '@/lib/api/models'
import { useModels, useModelVoices } from '@/lib/hooks/use-models'
import { useTranslation } from '@/lib/hooks/use-translation'
import { VIENEU_VOICES, languageModel, speechLanguage } from '@/lib/tts-voices'
import type { TtsVoice } from '@/lib/types/models'
import { preparedVoiceSample } from '@/lib/voice-preview-cache'

interface VoiceSelectorProps {
  language?: string
  onModelChange?: (model: string) => void
  id: string
  modelId: string
  value: string
  onChange: (voice: string) => void
  onBlur?: () => void
}

let activePreview: HTMLAudioElement | null = null

export function VoiceSelector({ id, modelId, language, value, onChange, onModelChange, onBlur }: VoiceSelectorProps) {
  const { t } = useTranslation()
  const [localLanguage, setLocalLanguage] = useState(value.startsWith('en-') ? 'en' : 'vi')
  const lang = speechLanguage(language || localLanguage)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(false)
  const [gender, setGender] = useState('all')
  const controller = useRef<AbortController | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const { data: models = [] } = useModels()
  const { data: discovered, isFetching } = useModelVoices(modelId, lang)
  const model = models.find(item => item.id === modelId)
  const fallback: TtsVoice[] = model?.name.toLowerCase().startsWith('vieneu') && lang === 'vi'
    ? VIENEU_VOICES.map(name => ({ id: name, name }))
    : (!modelId || model?.provider === 'edgetts')
      ? ['vi-VN-NamMinhNeural', 'vi-VN-HoaiMyNeural', 'en-US-GuyNeural', 'en-US-JennyNeural', 'en-US-AndrewMultilingualNeural', 'en-US-AvaMultilingualNeural', 'en-US-EmmaMultilingualNeural'].filter(name => name.startsWith(lang)).map(name => ({ id: name, name }))
      : []
  const voices = discovered ?? fallback
  const custom = !voices.some(voice => voice.id === value)
  const effectiveModel = voices.find(voice => voice.id === value)?.model_id || modelId
  const firstVoiceId = voices[0]?.id
  const shownVoices = gender === 'all' ? voices : voices.filter(voice => voice.gender?.toLowerCase() === gender)
  const selectedVoice = voices.find(voice => voice.id === value)
  const firstFilteredVoice = shownVoices[0]?.id
  const selectedVoiceId = selectedVoice?.id
  const selectedGender = selectedVoice?.gender?.toLowerCase()
  useEffect(() => {
    if (gender !== 'all' && firstFilteredVoice && selectedVoiceId && selectedGender !== gender) onChange(firstFilteredVoice)
  }, [gender, firstFilteredVoice, selectedVoiceId, selectedGender, onChange])

  useEffect(() => {
    for (const voice of discovered || []) void preparedVoiceSample(voice, modelId, lang)?.catch(() => {})
  }, [discovered, modelId, lang])
  useEffect(() => {
    const vietnamese = value.startsWith('vi-') || VIENEU_VOICES.some(name => name === value)
    if (firstVoiceId && ((lang === 'en' && vietnamese) || (lang === 'vi' && value.startsWith('en-')))) onChange(firstVoiceId)
  }, [lang, value, firstVoiceId, onChange])

  useEffect(() => {
    if (!language && value.startsWith('en-')) setLocalLanguage('en')
    else if (!language && (value.startsWith('vi-') || VIENEU_VOICES.some(name => name === value))) setLocalLanguage('vi')
  }, [language, value])

  useEffect(() => {
    if (discovered?.[0]?.model_id && discovered[0].model_id !== modelId) onModelChange?.(discovered[0].model_id)
  }, [discovered, modelId, onModelChange])
  useEffect(() => {
    controller.current?.abort()
    setPreviewUrl(null)
    setPreviewLoading(false)
    setPreviewError(false)
    const audio = audioRef.current
    return () => {
      controller.current?.abort()
      audio?.pause()
      if (activePreview === audio) activePreview = null
    }
  }, [modelId, value, lang])
  useEffect(() => {
    const audio = audioRef.current
    return () => {
      audio?.pause()
      if (activePreview === audio) activePreview = null
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  async function preview() {
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setPreviewLoading(true)
    setPreviewError(false)
    setPreviewUrl(null)
    try {
      const ready = selectedVoice && preparedVoiceSample(selectedVoice, effectiveModel, lang)
      const blob = await (ready || modelsApi.previewVoice(effectiveModel, value, lang, request.signal))
      if (!request.signal.aborted) setPreviewUrl(URL.createObjectURL(blob))
    } catch {
      if (!request.signal.aborted) setPreviewError(true)
    } finally {
      if (!request.signal.aborted) setPreviewLoading(false)
    }
  }

  return (
    <div className="space-y-2">
      {!language && <select aria-label={t('podcasts.voiceLanguage')} value={localLanguage}
        onChange={event => {
          const next = event.target.value
          setLocalLanguage(next)
          onModelChange?.(languageModel(models, next, modelId))
          onChange(next === 'en' ? 'en-US-GuyNeural' : 'Hải Đăng')
        }} className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
        <option value="vi">{t('podcasts.voiceVietnamese')}</option>
        <option value="en">{t('podcasts.voiceEnglish')}</option>
      </select>}
      <select aria-label={t('podcasts.voiceGender')} value={gender}
        onChange={event => {
          const next = event.target.value
          setGender(next)
          if (next !== 'all' && selectedVoice?.gender?.toLowerCase() !== next) {
            const first = voices.find(voice => voice.gender?.toLowerCase() === next)
            if (first) onChange(first.id)
          }
        }} className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
        <option value="all">{t('podcasts.voiceAllGenders')}</option>
        <option value="male">{t('podcasts.voiceMale')}</option>
        <option value="female">{t('podcasts.voiceFemale')}</option>
      </select>
      <select
        id={id}
        value={custom ? '__custom__' : value}
        onChange={event => onChange(event.target.value === '__custom__' ? '' : event.target.value)}
        onBlur={onBlur}
        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
      >
        {['male', 'female', 'unknown'].map(group => {
          const items = shownVoices.filter(voice => (voice.gender?.toLowerCase() || 'unknown') === group)
          return items.length ? <optgroup key={group} label={t(group === 'male' ? 'podcasts.voiceMale' : group === 'female' ? 'podcasts.voiceFemale' : 'podcasts.voiceOtherGender')}>
            {items.map(voice => <option key={voice.id} value={voice.id}>
              {voice.name}{voice.description ? ` · ${voice.description}` : ''}
            </option>)}
          </optgroup> : null
        })}
        <option value="__custom__">{t('podcasts.customVoice')}</option>
      </select>
      {selectedVoice?.gender && <p className="text-xs text-muted-foreground">
        {t(selectedVoice.gender.toLowerCase() === 'male' ? 'podcasts.voiceMale' : selectedVoice.gender.toLowerCase() === 'female' ? 'podcasts.voiceFemale' : 'podcasts.voiceOtherGender')}
      </p>}
      <Button type="button" variant="outline" onClick={preview} disabled={custom || !effectiveModel || previewLoading || isFetching}>
        {previewLoading ? t('podcasts.voicePreviewLoading') : t('podcasts.voicePreview')}
      </Button>
      {custom && (
        <Input
          aria-label={t('podcasts.voiceId')}
          value={value}
          onChange={event => onChange(event.target.value)}
          onBlur={onBlur}
          autoComplete="off"
        />
      )}
      {previewError && <p role="alert" className="text-sm text-destructive">{t('podcasts.voicePreviewError')}</p>}
      {previewUrl && <audio ref={audioRef} src={previewUrl} controls autoPlay className="w-full" onError={() => setPreviewError(true)}
        aria-label={t('podcasts.voicePreview')} onPlay={() => {
          if (activePreview && activePreview !== audioRef.current) activePreview.pause()
          activePreview = audioRef.current
        }} />}
    </div>
  )
}
