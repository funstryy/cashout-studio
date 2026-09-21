import { createI18n } from 'vue-i18n'
import ru from '../locales/ru'
import en from '../locales/en'

export type LocaleCode = 'ru' | 'en'
const STORAGE_KEY = 'cashout_studio_locale'

function detectInitialLocale(): LocaleCode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'ru' || saved === 'en') return saved
  } catch {
    // localStorage unavailable (private browsing) - fall through to default.
  }
  return 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: detectInitialLocale(),
  fallbackLocale: 'en',
  messages: { ru, en },
})

export function setLocale(locale: LocaleCode) {
  ;(i18n.global.locale as any).value = locale
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // ignore - just won't persist across reloads
  }
}

export function currentLocale(): LocaleCode {
  return (i18n.global.locale as any).value
}
