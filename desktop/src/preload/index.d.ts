import { ElectronAPI } from '@electron-toolkit/preload'

export type CodexMiniAPI = {
  openWorkspaceDirectory(defaultPath?: string): Promise<string | null>
  createDefaultChatDirectory(): Promise<string>
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: CodexMiniAPI
  }
}
