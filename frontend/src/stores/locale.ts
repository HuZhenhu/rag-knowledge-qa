import { defineStore } from 'pinia'
import { ref } from 'vue'
import i18n from '../i18n'

export const useLocaleStore = defineStore('locale', () => {
  const currentLocale = ref<'zh' | 'en'>(
    (localStorage.getItem('locale') as 'zh' | 'en') || 'zh'
  )

  const setLocale = (locale: 'zh' | 'en') => {
    currentLocale.value = locale
    localStorage.setItem('locale', locale)
    i18n.global.locale.value = locale
  }

  const toggleLocale = () => {
    const newLocale = currentLocale.value === 'zh' ? 'en' : 'zh'
    setLocale(newLocale)
  }

  // Initialize i18n locale on store creation
  i18n.global.locale.value = currentLocale.value

  return {
    currentLocale,
    setLocale,
    toggleLocale,
  }
})
