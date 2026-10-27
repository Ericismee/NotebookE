import apiClient from './client'
import { getApiUrl } from '@/lib/config'

export interface VideoEpisode {
  id: string
  name: string
  notebook_id?: string
  prompt: string
  video_file?: string
  video_url?: string
  created?: string
  job_status?: string
  error_message?: string
  command_id?: string
}

export interface VideoGenerationRequest {
  name: string
  content?: string
  notebook_id?: string
  n_scenes?: number
  duration_minutes?: 1 | 3 | 5
  image_workflow?: string
  visual_mode?: string
  tts_voice?: string
  voice_model?: string
  language?: string
  video_type?: string
  style?: string
  character_id?: string
  custom_prompt?: string
  aspect_ratio?: string
}

export interface VideoGenerationResponse {
  job_id: string
  status: string
  message: string
  name: string
}

export interface VideoTemplate {
  id: string
  name: string
  description: string
  tone: 'light' | 'dark'
  content_types: string[]
  background: string
  panel: string
  ink: string
  muted: string
  accent: string
  accent_2: string
}

export const VIDEO_TEMPLATE_CHOICES = [
  { id: 'documentary-night', name: 'Phóng sự điện ảnh', description: 'Tối, giàu chiều sâu cho lịch sử và thời sự.', tone: 'dark', content_types: ['Lịch sử', 'Thời sự'], background: '#111827', panel: '#172033', ink: '#f8fafc', muted: '#cbd5e1', accent: '#f59e0b', accent_2: '#fb7185' },
  { id: 'editorial-light', name: 'Tạp chí hiện đại', description: 'Sáng, sạch và dễ đọc cho nội dung tổng hợp.', tone: 'light', content_types: ['Tổng hợp', 'Kinh doanh'], background: '#f7f4ee', panel: '#ffffff', ink: '#18212f', muted: '#5f6b7a', accent: '#2563eb', accent_2: '#e8793e' },
  { id: 'technical-grid', name: 'Bản vẽ công nghệ', description: 'Tối, chính xác cho kỹ thuật và kiến trúc hệ thống.', tone: 'dark', content_types: ['Công nghệ', 'Kỹ thuật'], background: '#08131f', panel: '#0d2030', ink: '#e6f6ff', muted: '#9fc5d8', accent: '#22d3ee', accent_2: '#60a5fa' },
  { id: 'academic-paper', name: 'Học thuật tinh gọn', description: 'Sáng, trang nhã cho nghiên cứu và bài giảng.', tone: 'light', content_types: ['Nghiên cứu', 'Giáo dục'], background: '#f3f1e9', panel: '#fffdf7', ink: '#1f2937', muted: '#667085', accent: '#4f46e5', accent_2: '#0f766e' },
  { id: 'data-dashboard', name: 'Dữ liệu quyết định', description: 'Tối, tương phản cao cho số liệu và tài chính.', tone: 'dark', content_types: ['Dữ liệu', 'Tài chính'], background: '#101418', panel: '#182027', ink: '#f7fafc', muted: '#aab8c5', accent: '#2dd4bf', accent_2: '#a3e635' },
  { id: 'nature-journal', name: 'Nhật ký tự nhiên', description: 'Sáng, hữu cơ cho sinh học và môi trường.', tone: 'light', content_types: ['Sinh học', 'Môi trường'], background: '#eef3e9', panel: '#fbfdf8', ink: '#243126', muted: '#657465', accent: '#3f7d5b', accent_2: '#d08c45' },
  { id: 'warm-story', name: 'Kể chuyện ấm áp', description: 'Sáng, gần gũi cho văn học và câu chuyện con người.', tone: 'light', content_types: ['Văn học', 'Nhân văn'], background: '#fbf0e8', panel: '#fffaf5', ink: '#352823', muted: '#7c665c', accent: '#c95f45', accent_2: '#d49b3f' },
  { id: 'cinematic-noir', name: 'Điện ảnh noir', description: 'Tối, kịch tính cho điều tra và chủ đề bí ẩn.', tone: 'dark', content_types: ['Điều tra', 'Bí ẩn'], background: '#121212', panel: '#1c1c1d', ink: '#f5f5f4', muted: '#b7b7b3', accent: '#ef4444', accent_2: '#f59e0b' },
  { id: 'health-calm', name: 'Sức khỏe an tâm', description: 'Sáng, dịu và đáng tin cho y tế và tâm lý.', tone: 'light', content_types: ['Y tế', 'Sức khỏe'], background: '#edf7f5', panel: '#fbfffe', ink: '#163331', muted: '#5c7773', accent: '#0f9f8f', accent_2: '#5b8def' },
  { id: 'future-neon', name: 'Tương lai số', description: 'Tối, sống động cho AI, không gian và đổi mới.', tone: 'dark', content_types: ['AI', 'Tương lai'], background: '#0c0b1d', panel: '#17142e', ink: '#f4f1ff', muted: '#b9b3d6', accent: '#8b5cf6', accent_2: '#22d3ee' },
] as const satisfies readonly VideoTemplate[]

const LEGACY_VIDEO_TEMPLATES: Record<string, string> = {
  'AI Visual Director': 'auto',
  'Documentary Cinematic': 'documentary-night',
  'Editorial Presentation': 'editorial-light',
  'Technical Visualization': 'technical-grid',
}

export function normalizeVideoTemplateId(value?: string | null): string {
  if (!value) return 'auto'
  return LEGACY_VIDEO_TEMPLATES[value] ?? value
}

export async function resolveVideoAssetUrl(path?: string | null): Promise<string | undefined> {
  if (!path) {
    return undefined
  }
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  const base = await getApiUrl()
  if (path.startsWith('/')) {
    return `${base}${path}`
  }
  return `${base}/${path}`
}

export const videosApi = {
  listTemplates: async () => {
    const response = await apiClient.get<{ templates: VideoTemplate[]; default: string }>('/videos/templates')
    return response.data
  },

  listEpisodes: async () => {
    const response = await apiClient.get<VideoEpisode[]>('/videos/episodes')
    return response.data
  },

  generateVideo: async (payload: VideoGenerationRequest) => {
    const response = await apiClient.post<VideoGenerationResponse>(
      '/videos/generate',
      payload
    )
    return response.data
  },

  deleteEpisode: async (episodeId: string) => {
    const response = await apiClient.delete(`/videos/episodes/${episodeId}`)
    return response.data
  },

  getJobStatus: async (jobId: string) => {
    const response = await apiClient.get<{ status: string; result?: unknown; error_message?: string }>(
      `/videos/jobs/${jobId}`
    )
    return response.data
  },
}
