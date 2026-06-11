import { ElectronAPI } from '@electron-toolkit/preload'

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type ThemeState = {
  mode: ThemeMode
  resolved: ResolvedTheme
}

export type CodexMiniAPI = {
  openWorkspaceDirectory(defaultPath?: string): Promise<string | null>
  createDefaultChatDirectory(): Promise<string>
  getTheme(): Promise<ThemeState>
  setThemeMode(mode: ThemeMode): Promise<ThemeState>
  onThemeChanged(callback: (state: ThemeState) => void): () => void
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: CodexMiniAPI
  }
}
