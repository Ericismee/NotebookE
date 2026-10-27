import { modelsApi } from '@/lib/api/models'
import type { TtsVoice } from '@/lib/types/models'

// Cache audio bytes, not object URLs: each player owns and releases its URL.
const samples = new Map<string, Promise<Blob>>()

export function preparedVoiceSample(voice: TtsVoice, modelId: string, language: string) {
  const key = voice.preview_key
  if (!key || !voice.preview_ready) return undefined
  let request = samples.get(key)
  if (!request) {
    request = modelsApi.cachedVoicePreview(voice.model_id || modelId, voice.id, language, key)
    samples.set(key, request)
    void request.catch(() => { if (samples.get(key) === request) samples.delete(key) })
    if (samples.size > 64) samples.delete(samples.keys().next().value!)
  }
  return request
}
