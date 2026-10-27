import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { VoiceSelector } from './VoiceSelector'
import { modelsApi } from '@/lib/api/models'

const discovery = vi.hoisted(() => ({ ready: false }))

vi.mock('@/lib/hooks/use-models', () => ({
  useModels: () => ({ data: [] }),
  useModelVoices: (_model: string, language: string) => ({ isFetching: false, data: (language === 'en'
    ? [{ id: 'en-US-JennyNeural', name: 'Jenny', gender: 'female', language_code: 'en-US', model_id: 'model:en' }, { id: 'en-US-GuyNeural', name: 'Guy', gender: 'male', language_code: 'en-US', model_id: 'model:en' }]
    : [{ id: 'Mai Anh', name: 'Mai Anh', gender: 'female', language_code: 'vi-VN', model_id: 'model:vi' }, { id: 'Hải Đăng', name: 'Hải Đăng', gender: 'male', language_code: 'vi-VN', model_id: 'model:vi' }]).map(voice => ({ ...voice, preview_ready: discovery.ready, preview_key: `ready-${voice.id}` })) }),
}))
vi.mock('@/lib/api/models', () => ({ modelsApi: { previewVoice: vi.fn(), cachedVoicePreview: vi.fn() } }))

describe('VoiceSelector preview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    discovery.ready = false
    URL.createObjectURL = vi.fn(() => 'blob:preview')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  })

  it('shows only the selected language and requests its matching model', async () => {
    vi.mocked(modelsApi.previewVoice).mockResolvedValue(new Blob(['audio']))
    render(<VoiceSelector id="voice" modelId="model:vi" language="en" value="en-US-JennyNeural" onChange={vi.fn()} />)
    expect(screen.getByRole('option', { name: 'Jenny' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Mai Anh' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.voicePreview' }))
    await waitFor(() => expect(modelsApi.previewVoice).toHaveBeenCalledWith('model:en', 'en-US-JennyNeural', 'en', expect.any(AbortSignal)))
    await waitFor(() => expect(document.querySelector('audio')).toHaveAttribute('src', 'blob:preview'))
  })

  it('cancels a pending preview when switching language', async () => {
    let resolve: (blob: Blob) => void = () => {}
    vi.mocked(modelsApi.previewVoice).mockImplementation(() => new Promise(done => { resolve = done }))
    const { rerender } = render(<VoiceSelector id="voice" modelId="model:vi" language="vi" value="Mai Anh" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.voicePreview' }))
    const signal = vi.mocked(modelsApi.previewVoice).mock.calls[0][3]
    rerender(<VoiceSelector id="voice" modelId="model:en" language="en" value="en-US-JennyNeural" onChange={vi.fn()} />)
    expect(signal?.aborted).toBe(true)
    resolve(new Blob(['old sample']))
    await waitFor(() => expect(screen.getByRole('button', { name: 'podcasts.voicePreview' })).toBeEnabled())
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(document.querySelector('audio')).toBeNull()
  })

  it('stops and releases an existing sample when changing language', async () => {
    vi.mocked(modelsApi.previewVoice).mockResolvedValue(new Blob(['audio']))
    const { rerender } = render(<VoiceSelector id="voice" modelId="model:vi" language="vi" value="Mai Anh" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.voicePreview' }))
    await waitFor(() => expect(document.querySelector('audio')).toBeInTheDocument())
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear()
    rerender(<VoiceSelector id="voice" modelId="model:en" language="en" value="en-US-JennyNeural" onChange={vi.fn()} />)
    await waitFor(() => expect(document.querySelector('audio')).toBeNull())
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })

  it('separates genders and selects a matching voice when filtering', () => {
    const onChange = vi.fn()
    render(<VoiceSelector id="voice" modelId="model:en" language="en" value="en-US-JennyNeural" onChange={onChange} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'podcasts.voiceGender' }), { target: { value: 'male' } })
    expect(screen.getByRole('option', { name: 'Guy' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Jenny' })).not.toBeInTheDocument()
    expect(onChange).toHaveBeenCalledWith('en-US-GuyNeural')
  })

  it('preloads ready samples and plays without a synthesis request', async () => {
    discovery.ready = true
    vi.mocked(modelsApi.cachedVoicePreview).mockResolvedValue(new Blob(['prepared audio']))
    render(<VoiceSelector id="voice" modelId="model:vi" language="vi" value="Mai Anh" onChange={vi.fn()} />)
    await waitFor(() => expect(modelsApi.cachedVoicePreview).toHaveBeenCalledWith('model:vi', 'Mai Anh', 'vi', 'ready-Mai Anh'))
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.voicePreview' }))
    await waitFor(() => expect(document.querySelector('audio')).toHaveAttribute('src', 'blob:preview'))
    expect(modelsApi.previewVoice).not.toHaveBeenCalled()
    expect(modelsApi.cachedVoicePreview).toHaveBeenCalledTimes(2)
  })
})
