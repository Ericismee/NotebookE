// VieNeu SDK 3.8.3 presets. Names are API identifiers, not translated UI copy.
// Custom/saved voices can still be entered in the editable voice field.
import type { Model } from '@/lib/types/models'

export function speechLanguage(language: string) {
  return language.toLowerCase().startsWith('en') ? 'en' : 'vi'
}

export function languageModel(models: Model[], language: string, current: string) {
  const english = speechLanguage(language) === 'en'
  return models.find(model => model.type === 'text_to_speech' &&
    (english ? model.provider === 'edgetts' : model.name.toLowerCase().startsWith('vieneu')))?.id || current
}
export const VIENEU_VOICES = [
  'Hải Đăng', 'Thiện Minh', 'Mai Anh', 'Minh Đức', 'Quốc Tuấn',
  'Ngọc Huyền', 'Ngọc Linh', 'Phạm Tuyên', 'Quỳnh Anh', 'Thanh Bình',
  'Trúc Ly', 'Xuân Vĩnh', 'Đoan Trang', 'Ngọc Trân', 'Quang Sơn',
  'Adam', 'Kim Thanh', 'Minh Triết', 'Mỹ Duyên', 'Thái Sơn',
  'Thùy Dung', 'Thục Đoan', 'Đức Trí',
  'Adam bựa', 'Thiền Tâm Đức',
] as const
