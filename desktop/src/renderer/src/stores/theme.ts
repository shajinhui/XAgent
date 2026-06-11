import { defineStore } from 'pinia'
import type { ResolvedTheme, ThemeMode, ThemeState } from '../../../preload'

const THEME_ORDER: ThemeMode[] = ['system', 'light', 'dark']

function applyThemeToDocument(state: ThemeState): void {
  const root = document.documentElement
  root.dataset.theme = state.resolved
  root.dataset.themeMode = state.mode
  root.style.colorScheme = state.resolved
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: 'system' as ThemeMode,
    resolved: 'dark' as ResolvedTheme,
    initialized: false,
    unsubscribeThemeChanged: null as (() => void) | null
  }),
  getters: {
    label: (state): string => {
      if (state.mode === 'light') return '浅色'
      if (state.mode === 'dark') return '深色'
      return '跟随系统'
    }
  },
  actions: {
    applyState(state: ThemeState): void {
      this.mode = state.mode
      this.resolved = state.resolved
      this.initialized = true
      applyThemeToDocument(state)
    },

    async initialize(): Promise<void> {
      if (this.unsubscribeThemeChanged) return

      this.applyState(await window.api.getTheme())
      this.unsubscribeThemeChanged = window.api.onThemeChanged((state) => {
        this.applyState(state)
      })
    },

    async setMode(mode: ThemeMode): Promise<void> {
      this.applyState(await window.api.setThemeMode(mode))
    },

    async cycleMode(): Promise<void> {
      const currentIndex = THEME_ORDER.indexOf(this.mode)
      const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % THEME_ORDER.length : 0
      await this.setMode(THEME_ORDER[nextIndex])
    }
  }
})
