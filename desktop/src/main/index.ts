import {
  app,
  dialog,
  ipcMain,
  nativeTheme,
  screen,
  shell,
  BrowserWindow,
  type OpenDialogOptions,
  type Rectangle
} from 'electron'
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { mkdir, readFile, writeFile } from 'fs/promises'
import { createConnection } from 'net'
import { homedir } from 'os'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'

const BACKEND_HOST = '127.0.0.1'
const BACKEND_PORT = 8000
type ThemeMode = 'system' | 'light' | 'dark'
type ResolvedTheme = 'light' | 'dark'
type ThemeState = { mode: ThemeMode; resolved: ResolvedTheme }
type WindowBoundsState = {
  width: number
  height: number
  x?: number
  y?: number
  isMaximized?: boolean
}

const THEME_CONFIG_FILE = 'theme-preferences.json'
const WINDOW_STATE_FILE = 'window-state.json'
const DEFAULT_WINDOW_BOUNDS: WindowBoundsState = { width: 1080, height: 936 }
const MIN_WINDOW_WIDTH = 760
const MIN_WINDOW_HEIGHT = 760
const WINDOW_STATE_SAVE_DELAY_MS = 250
const THEME_WINDOW_BACKGROUND: Record<ResolvedTheme, string> = {
  dark: '#20252d',
  light: '#f4f6f8'
}
let backendProcess: ChildProcessWithoutNullStreams | null = null
let themeMode: ThemeMode = 'system'
let windowBoundsState: WindowBoundsState | null = null
let windowStateSaveTimer: ReturnType<typeof setTimeout> | null = null

function resolveBackendRoot(): string {
  if (is.dev) {
    return join(app.getAppPath(), '..')
  }

  return join(process.resourcesPath, 'backend')
}

function resolvePythonExecutable(backendRoot: string): string {
  const venvPython =
    process.platform === 'win32'
      ? join(backendRoot, '.venv', 'Scripts', 'python.exe')
      : join(backendRoot, '.venv', 'bin', 'python')

  return existsSync(venvPython) ? venvPython : 'python3'
}

function canConnectToBackend(): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ host: BACKEND_HOST, port: BACKEND_PORT })

    socket.setTimeout(250)
    socket.once('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.once('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.once('error', () => {
      socket.destroy()
      resolve(false)
    })
  })
}

async function waitForBackend(timeoutMs = 6000): Promise<boolean> {
  const startedAt = Date.now()

  while (Date.now() - startedAt < timeoutMs) {
    if (await canConnectToBackend()) return true
    await new Promise((resolve) => setTimeout(resolve, 180))
  }

  return false
}

async function startBackend(): Promise<void> {
  if (await canConnectToBackend()) return

  const backendRoot = resolveBackendRoot()
  const python = resolvePythonExecutable(backendRoot)

  backendProcess = spawn(
    python,
    ['-m', 'uvicorn', 'server.app:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)],
    {
      cwd: backendRoot,
      env: process.env,
      stdio: 'pipe'
    }
  )

  backendProcess.stdout.on('data', (chunk) => {
    console.log(`[backend] ${chunk.toString().trim()}`)
  })
  backendProcess.stderr.on('data', (chunk) => {
    console.error(`[backend] ${chunk.toString().trim()}`)
  })
  backendProcess.on('exit', () => {
    backendProcess = null
  })

  await waitForBackend()
}

function stopBackend(): void {
  backendProcess?.kill()
  backendProcess = null
}

function formatLocalDatePathSegment(date = new Date()): string {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  })
  return formatter.format(date)
}

async function createDefaultChatDirectory(): Promise<string> {
  const targetPath = join(homedir(), 'Documents', 'Codex', formatLocalDatePathSegment(), 'new-chat')
  await mkdir(targetPath, { recursive: true })
  return targetPath
}

function normalizeThemeMode(value: unknown): ThemeMode {
  if (value === 'light' || value === 'dark' || value === 'system') {
    return value
  }
  return 'system'
}

function resolveTheme(): ResolvedTheme {
  return nativeTheme.shouldUseDarkColors ? 'dark' : 'light'
}

function getThemeState(): ThemeState {
  return {
    mode: themeMode,
    resolved: resolveTheme()
  }
}

function themeConfigPath(): string {
  return join(app.getPath('userData'), THEME_CONFIG_FILE)
}

async function loadThemePreference(): Promise<void> {
  try {
    const raw = await readFile(themeConfigPath(), 'utf8')
    const parsed = JSON.parse(raw) as { mode?: unknown }
    themeMode = normalizeThemeMode(parsed.mode)
  } catch {
    themeMode = 'system'
  }
  nativeTheme.themeSource = themeMode
}

async function saveThemePreference(mode: ThemeMode): Promise<void> {
  await writeFile(themeConfigPath(), JSON.stringify({ mode }, null, 2), 'utf8')
}

function windowStatePath(): string {
  return join(app.getPath('userData'), WINDOW_STATE_FILE)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function normalizeWindowState(value: unknown): WindowBoundsState | null {
  if (!value || typeof value !== 'object') return null

  const record = value as Record<string, unknown>
  if (!isFiniteNumber(record.width) || !isFiniteNumber(record.height)) return null

  const state: WindowBoundsState = {
    width: Math.max(MIN_WINDOW_WIDTH, Math.round(record.width)),
    height: Math.max(MIN_WINDOW_HEIGHT, Math.round(record.height)),
    isMaximized: record.isMaximized === true
  }

  if (isFiniteNumber(record.x) && isFiniteNumber(record.y)) {
    state.x = Math.round(record.x)
    state.y = Math.round(record.y)
  }

  return state
}

async function loadWindowState(): Promise<void> {
  try {
    const raw = await readFile(windowStatePath(), 'utf8')
    windowBoundsState = normalizeWindowState(JSON.parse(raw))
  } catch {
    windowBoundsState = null
  }
}

function normalizeSavedBounds(window: BrowserWindow): WindowBoundsState {
  const bounds = window.getNormalBounds()
  const state: WindowBoundsState = {
    width: Math.max(MIN_WINDOW_WIDTH, Math.round(bounds.width)),
    height: Math.max(MIN_WINDOW_HEIGHT, Math.round(bounds.height)),
    isMaximized: window.isMaximized()
  }

  if (isFiniteNumber(bounds.x) && isFiniteNumber(bounds.y)) {
    state.x = Math.round(bounds.x)
    state.y = Math.round(bounds.y)
  }

  return state
}

async function saveWindowState(window: BrowserWindow): Promise<void> {
  if (window.isDestroyed()) return

  windowBoundsState = normalizeSavedBounds(window)
  await mkdir(app.getPath('userData'), { recursive: true })
  await writeFile(windowStatePath(), JSON.stringify(windowBoundsState, null, 2), 'utf8')
}

function saveWindowStateSync(window: BrowserWindow): void {
  if (window.isDestroyed()) return

  windowBoundsState = normalizeSavedBounds(window)
  mkdirSync(app.getPath('userData'), { recursive: true })
  writeFileSync(windowStatePath(), JSON.stringify(windowBoundsState, null, 2), 'utf8')
}

function queueWindowStateSave(window: BrowserWindow): void {
  if (window.isDestroyed()) return

  if (windowStateSaveTimer) {
    clearTimeout(windowStateSaveTimer)
  }

  windowStateSaveTimer = setTimeout(() => {
    windowStateSaveTimer = null
    void saveWindowState(window).catch((error) => {
      console.error(`Failed to save window state: ${String(error)}`)
    })
  }, WINDOW_STATE_SAVE_DELAY_MS)
}

function flushWindowStateSave(window: BrowserWindow): void {
  if (windowStateSaveTimer) {
    clearTimeout(windowStateSaveTimer)
    windowStateSaveTimer = null
  }

  try {
    saveWindowStateSync(window)
  } catch (error) {
    console.error(`Failed to save window state: ${String(error)}`)
  }
}

function doRectanglesOverlap(a: Rectangle, b: Rectangle): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
}

function getInitialWindowBounds(): WindowBoundsState {
  const saved = windowBoundsState ?? DEFAULT_WINDOW_BOUNDS
  const primaryWorkArea = screen.getPrimaryDisplay().workArea
  const state: WindowBoundsState = {
    width: Math.max(MIN_WINDOW_WIDTH, Math.min(saved.width, primaryWorkArea.width)),
    height: Math.max(MIN_WINDOW_HEIGHT, Math.min(saved.height, primaryWorkArea.height)),
    isMaximized: saved.isMaximized
  }

  if (isFiniteNumber(saved.x) && isFiniteNumber(saved.y)) {
    const restoredBounds: Rectangle = {
      x: saved.x,
      y: saved.y,
      width: state.width,
      height: state.height
    }
    const isVisible = screen
      .getAllDisplays()
      .some((display) => doRectanglesOverlap(restoredBounds, display.workArea))

    if (isVisible) {
      state.x = saved.x
      state.y = saved.y
    }
  }

  return state
}

function syncWindowTheme(window: BrowserWindow, state = getThemeState()): void {
  window.setBackgroundColor(THEME_WINDOW_BACKGROUND[state.resolved])
  window.webContents.send('theme:changed', state)
}

function syncAllWindowThemes(): ThemeState {
  const state = getThemeState()
  for (const window of BrowserWindow.getAllWindows()) {
    syncWindowTheme(window, state)
  }
  return state
}

function createWindow(): void {
  const isMac = process.platform === 'darwin'
  const themeState = getThemeState()
  const initialWindowBounds = getInitialWindowBounds()

  const mainWindow = new BrowserWindow({
    width: initialWindowBounds.width,
    height: initialWindowBounds.height,
    x: initialWindowBounds.x,
    y: initialWindowBounds.y,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    show: false,
    autoHideMenuBar: true,
    resizable: true,
    backgroundColor: THEME_WINDOW_BACKGROUND[themeState.resolved],
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    title: 'XCode',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  if (initialWindowBounds.isMaximized) {
    mainWindow.maximize()
  }

  if (isMac) {
    mainWindow.setWindowButtonPosition({ x: 18, y: 12 })
  }

  mainWindow.on('resize', () => queueWindowStateSave(mainWindow))
  mainWindow.on('move', () => queueWindowStateSave(mainWindow))
  mainWindow.on('maximize', () => queueWindowStateSave(mainWindow))
  mainWindow.on('unmaximize', () => queueWindowStateSave(mainWindow))
  mainWindow.on('close', () => flushWindowStateSave(mainWindow))

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.once('did-finish-load', () => {
    syncWindowTheme(mainWindow)
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // 基于 electron-vite CLI 的渲染进程热更新（HMR）。
  // 开发时加载远程 URL，生产时加载本地 HTML 文件。
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// 当 Electron 初始化完成并准备创建浏览器窗口时会调用此方法。
// 某些 API 只能在该事件发生后使用。
app.whenReady().then(() => {
  // 为 Windows 设置应用用户模型 ID
  electronApp.setAppUserModelId('com.codexmini.desktop')

  // 开发环境下默认通过 F12 打开或关闭开发者工具（DevTools），
  // 生产环境中忽略 CommandOrControl + R 快捷键。
  // 详情见 https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  ipcMain.handle('workspace:select-directory', async (event, defaultPath?: string) => {
    const owner = BrowserWindow.fromWebContents(event.sender) ?? undefined
    const options: OpenDialogOptions = {
      title: '打开工作区',
      defaultPath,
      properties: ['openDirectory']
    }
    const result = owner
      ? await dialog.showOpenDialog(owner, options)
      : await dialog.showOpenDialog(options)

    if (result.canceled || !result.filePaths.length) {
      return null
    }

    return result.filePaths[0]
  })

  ipcMain.handle('workspace:create-default-chat-directory', async () => {
    return createDefaultChatDirectory()
  })

  ipcMain.handle('theme:get', () => {
    return getThemeState()
  })

  ipcMain.handle('theme:set-mode', async (_event, mode: unknown) => {
    themeMode = normalizeThemeMode(mode)
    nativeTheme.themeSource = themeMode
    try {
      await saveThemePreference(themeMode)
    } catch (error) {
      console.error(`Failed to save theme preference: ${String(error)}`)
    }
    return syncAllWindowThemes()
  })

  nativeTheme.on('updated', () => {
    syncAllWindowThemes()
  })

  void Promise.all([loadThemePreference(), loadWindowState()])
    .then(() => startBackend())
    .finally(() => {
      createWindow()
    })

  app.on('activate', function () {
    // 在 macOS 中，当 Dock 图标被点击且没有其他窗口打开时，通常会重新创建一个窗口。
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// 当所有窗口都关闭时退出应用（macOS 除外）。在 macOS 中，应用及其菜单栏通常会保持活动状态，直到用户使用 Cmd + Q 明确退出。
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  stopBackend()
})

// 你可以在此文件中包含应用主进程的其余特定代码，或者将它们放在单独的文件中并在此处引用。
