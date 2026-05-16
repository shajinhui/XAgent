import { app, dialog, ipcMain, shell, BrowserWindow, type OpenDialogOptions } from 'electron'
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'
import { existsSync } from 'fs'
import { mkdir } from 'fs/promises'
import { createConnection } from 'net'
import { homedir } from 'os'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'

const BACKEND_HOST = '127.0.0.1'
const BACKEND_PORT = 8000
let backendProcess: ChildProcessWithoutNullStreams | null = null

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

function createWindow(): void {
  const isMac = process.platform === 'darwin'

  const mainWindow = new BrowserWindow({
    width: 900,
    height: 900,
    minWidth: 760,
    minHeight: 760,
    show: false,
    autoHideMenuBar: true,
    resizable: true,
    backgroundColor: '#20252d',
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    title: 'Codex-mini',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  if (isMac) {
    mainWindow.setWindowButtonPosition({ x: 18, y: 12 })
  }

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
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

  void startBackend().finally(() => {
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
