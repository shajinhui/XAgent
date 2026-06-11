import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type ThemeState = { mode: ThemeMode; resolved: ResolvedTheme }

// Custom APIs for renderer
const api = {
  openWorkspaceDirectory(defaultPath?: string): Promise<string | null> {
    return ipcRenderer.invoke('workspace:select-directory', defaultPath)
  },
  createDefaultChatDirectory(): Promise<string> {
    return ipcRenderer.invoke('workspace:create-default-chat-directory')
  },
  getTheme(): Promise<ThemeState> {
    return ipcRenderer.invoke('theme:get')
  },
  setThemeMode(mode: ThemeMode): Promise<ThemeState> {
    return ipcRenderer.invoke('theme:set-mode', mode)
  },
  onThemeChanged(callback: (state: ThemeState) => void): () => void {
    const listener = (_event: IpcRendererEvent, state: ThemeState): void => {
      callback(state)
    }
    ipcRenderer.on('theme:changed', listener)
    return () => ipcRenderer.removeListener('theme:changed', listener)
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
