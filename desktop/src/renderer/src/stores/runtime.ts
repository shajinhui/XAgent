import { defineStore } from 'pinia'
import { RuntimeSocket, type RuntimeConnectionStatus } from '@renderer/services/runtimeSocket'
import { useChatStore } from '@renderer/stores/chat'
import type { ActivityStepKind } from '@renderer/stores/chat'
import type {
  ClarificationRequestEvent,
  ClarificationResponsePayload,
  PermissionRequestEvent,
  RuntimeEvent,
  RuntimeModelConfig,
  RuntimeReasoningEffort,
  RuntimeSessionState,
  RuntimeSessionSummary,
  RuntimeAdditionalRoot,
  RuntimeToolMetadataMap,
  RuntimeWorkspace,
  RuntimeWorkspaceProject,
  RuntimeWorkspaceTrust
} from '@renderer/types/runtimeEvents'

const DEFAULT_ENDPOINT = import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8000/agent/ws'
const MAX_EVENTS = 120
const RECONNECT_DELAY_MS = 1500
const WORKSPACE_PROJECTS_STORAGE_KEY = 'codex-mini.workspace-projects'
const WORKSPACE_SESSIONS_STORAGE_KEY = 'codex-mini.workspace-sessions'
const CONVERSATION_WORKSPACES_STORAGE_KEY = 'codex-mini.conversation-workspaces'
const MAX_WORKSPACE_PROJECTS = 20
const MAX_CONVERSATION_WORKSPACES = 12
const FALLBACK_MODEL_OPTIONS = [
  'openai/gpt-4o-mini',
  'deepseek/deepseek-chat',
  'deepseek/deepseek-reasoner'
]
const FALLBACK_REASONING_OPTIONS: RuntimeReasoningEffort[] = ['off', 'low', 'medium', 'high', 'max']

let runtimeSocket: RuntimeSocket | null = null
let reconnectTimer: number | null = null
let manualDisconnect = false

function formatToolLabel(event: PermissionRequestEvent): string {
  return event.tool || event.request_id || 'tool'
}

function formatClarificationLabel(event: ClarificationRequestEvent): string {
  return event.question || event.request_id || '需要确认问题'
}

function formatClarificationDetail(event: ClarificationRequestEvent): string | undefined {
  if (!event.options.length) return undefined

  return event.options
    .map((option, index) => {
      const recommended = option.recommended ? '（推荐）' : ''
      const description = option.description ? ` - ${option.description}` : ''
      return `${index + 1}. ${option.label}${recommended}${description}`
    })
    .join('\n')
}

function parseToolArguments(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || '{}')
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}

function getToolKind(name: string): ActivityStepKind {
  if (name === 'grep') return 'search'
  if (name === 'read_file') return 'read'
  if (name === 'write_file' || name === 'edit_file') return 'edit'
  if (name === 'run_command') return 'command'
  if (name === 'web_fetch') return 'web'
  return 'tool'
}

function formatToolStartLabel(name: string, rawArguments: string): string {
  const args = parseToolArguments(rawArguments)
  const path = typeof args.path === 'string' ? args.path : ''

  switch (name) {
    case 'grep':
      return path ? `正在探索 ${path}` : '正在探索项目'
    case 'read_file':
      return path ? `正在读取 ${path}` : '正在读取文件'
    case 'write_file':
    case 'edit_file':
      return path ? `正在编辑 ${path}` : '正在编辑文件'
    case 'run_command':
      return '正在准备运行命令'
    case 'web_fetch':
      return '正在获取网页'
    default:
      return `正在调用 ${name}`
  }
}

function formatToolResultLabel(name: string, ok: boolean): string {
  if (!ok) {
    switch (name) {
      case 'grep':
        return '探索失败'
      case 'read_file':
        return '读取失败'
      case 'write_file':
      case 'edit_file':
        return '编辑失败'
      case 'run_command':
        return '命令失败'
      case 'web_fetch':
        return '获取失败'
      default:
        return `工具失败：${name}`
    }
  }

  switch (name) {
    case 'grep':
      return '已探索 1 组结果'
    case 'read_file':
      return '已读取 1 个文件'
    case 'write_file':
    case 'edit_file':
      return '已编辑 1 个文件'
    case 'run_command':
      return '已运行 1 条命令'
    case 'web_fetch':
      return '已获取 1 个网页'
    default:
      return `已完成 ${name}`
  }
}

function formatToolDetail(value: unknown): string | undefined {
  if (!value) return undefined
  if (typeof value !== 'string') return String(value)

  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

function clearReconnectTimer(): void {
  if (!reconnectTimer) return

  window.clearTimeout(reconnectTimer)
  reconnectTimer = null
}

function normalizeSelectedRoot(root: string): string {
  return root.replace(/[\\/]+$/, '')
}

function isDefaultConversationSelectedRoot(root: string): boolean {
  return /[\\/]Documents[\\/]Codex[\\/]\d{4}-\d{2}-\d{2}[\\/]new-chat$/.test(
    normalizeSelectedRoot(root)
  )
}

function normalizeModelOption(value: unknown): string | null {
  if (typeof value !== 'string') return null

  const model = value.trim()
  if (!model || model.length > 160 || /\s/.test(model) || !model.includes('/')) return null
  return model
}

function uniqueModelOptions(values: unknown[], fallback = FALLBACK_MODEL_OPTIONS): string[] {
  const options = values
    .map((value) => normalizeModelOption(value))
    .filter((value): value is string => Boolean(value))

  return Array.from(new Set([...options, ...fallback]))
}

function normalizeReasoningEffort(value: unknown): RuntimeReasoningEffort | null {
  if (typeof value !== 'string') return null
  const effort = value.trim().toLowerCase()
  if (FALLBACK_REASONING_OPTIONS.includes(effort as RuntimeReasoningEffort)) {
    return effort as RuntimeReasoningEffort
  }
  return null
}

function normalizeReasoningOptions(values: unknown[]): RuntimeReasoningEffort[] {
  const options = values
    .map((value) => normalizeReasoningEffort(value))
    .filter((value): value is RuntimeReasoningEffort => Boolean(value))

  return Array.from(new Set([...options, ...FALLBACK_REASONING_OPTIONS]))
}

function loadStoredWorkspaceProjectCandidates(): unknown[] {
  try {
    const rawValue = window.localStorage.getItem(WORKSPACE_PROJECTS_STORAGE_KEY)
    const parsed = JSON.parse(rawValue || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function loadWorkspaceProjects(): RuntimeWorkspaceProject[] {
  return loadStoredWorkspaceProjectCandidates()
    .filter(isWorkspaceProject)
    .filter((project) => !isDefaultConversationSelectedRoot(project.selected_root))
    .slice(0, MAX_WORKSPACE_PROJECTS)
}

function loadConversationSelectedRoots(): string[] {
  const roots = new Set<string>()

  try {
    const rawValue = window.localStorage.getItem(CONVERSATION_WORKSPACES_STORAGE_KEY)
    const parsed = JSON.parse(rawValue || '[]')
    if (Array.isArray(parsed)) {
      parsed.forEach((root) => {
        if (typeof root === 'string' && root.trim()) {
          roots.add(normalizeSelectedRoot(root.trim()))
        }
      })
    }
  } catch {
    // Ignore stale localStorage data; new-chat roots can be learned again when opened.
  }

  loadStoredWorkspaceProjectCandidates().forEach((value) => {
    if (!isWorkspaceProject(value)) return
    if (isDefaultConversationSelectedRoot(value.selected_root)) {
      roots.add(normalizeSelectedRoot(value.selected_root))
    }
  })

  return Array.from(roots).slice(0, MAX_CONVERSATION_WORKSPACES)
}

function loadWorkspaceSessionCache(): Record<string, RuntimeSessionSummary[]> {
  try {
    const rawValue = window.localStorage.getItem(WORKSPACE_SESSIONS_STORAGE_KEY)
    const parsed = JSON.parse(rawValue || '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}

    return Object.fromEntries(
      Object.entries(parsed)
        .filter(([root, sessions]) => typeof root === 'string' && Array.isArray(sessions))
        .map(([root, sessions]) => [
          normalizeSelectedRoot(root),
          (sessions as unknown[]).filter(isSessionSummary).slice(0, 50)
        ])
    )
  } catch {
    return {}
  }
}

function isWorkspaceTrust(value: unknown): value is RuntimeWorkspaceTrust {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeWorkspaceTrust>
  return (
    (candidate.level === 'trusted' ||
      candidate.level === 'untrusted' ||
      candidate.level === 'session_only') &&
    typeof candidate.trust_key === 'string' &&
    (candidate.source === 'default' ||
      candidate.source === 'user_config' ||
      candidate.source === 'session') &&
    typeof candidate.project_config_enabled === 'boolean'
  )
}

function isAdditionalRoot(value: unknown): value is RuntimeAdditionalRoot {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeAdditionalRoot>
  return (
    typeof candidate.path === 'string' &&
    (candidate.access === 'read' || candidate.access === 'write') &&
    (candidate.source === 'user' ||
      candidate.source === 'session' ||
      candidate.source === 'config')
  )
}

function isWorkspaceProject(value: unknown): value is RuntimeWorkspaceProject {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeWorkspaceProject>
  return (
    typeof candidate.selected_root === 'string' &&
    typeof candidate.project_root === 'string' &&
    typeof candidate.current_dir === 'string' &&
    typeof candidate.display_name === 'string' &&
    (typeof candidate.git_root === 'string' || candidate.git_root === null) &&
    isWorkspaceTrust(candidate.trust) &&
    Array.isArray(candidate.additional_roots) &&
    candidate.additional_roots.every(isAdditionalRoot) &&
    typeof candidate.updated_at === 'number'
  )
}

function isSessionSummary(value: unknown): value is RuntimeSessionSummary {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeSessionSummary>
  return (
    typeof candidate.session_id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.created_at === 'number' &&
    typeof candidate.updated_at === 'number' &&
    (typeof candidate.last_turn_id === 'string' || candidate.last_turn_id === null) &&
    typeof candidate.message_count === 'number' &&
    typeof candidate.last_message === 'string'
  )
}

export const useRuntimeStore = defineStore('runtime', {
  state: () => ({
    endpoint: DEFAULT_ENDPOINT,
    connectionStatus: 'idle' as RuntimeConnectionStatus,
    sessionId: '',
    schemaVersion: '',
    activeTurnId: '',
    errorMessage: '',
    sessionState: null as RuntimeSessionState | null,
    activePermission: null as PermissionRequestEvent | null,
    activeClarification: null as ClarificationRequestEvent | null,
    sessionHistory: [] as RuntimeSessionSummary[],
    sessionsLoading: false,
    selectedSessionId: '',
    workspace: null as RuntimeWorkspace | null,
    workspaceProjects: loadWorkspaceProjects(),
    conversationSelectedRoots: loadConversationSelectedRoots(),
    sessionsBySelectedRoot: loadWorkspaceSessionCache(),
    pendingWorkspaceResume: null as { selected_root: string; sessionId: string } | null,
    pendingConversationSelectedRoot: null as string | null,
    selectedModel: FALLBACK_MODEL_OPTIONS[0],
    modelOptions: [...FALLBACK_MODEL_OPTIONS],
    reasoningEffort: 'off' as RuntimeReasoningEffort,
    reasoningEffortOptions: [...FALLBACK_REASONING_OPTIONS],
    tools: {} as RuntimeToolMetadataMap,
    events: [] as RuntimeEvent[]
  }),
  getters: {
    isConnected: (state) => state.connectionStatus === 'connected',
    isConnecting: (state) => state.connectionStatus === 'connecting',
    isSuspended: (state) => Boolean(state.sessionState?.suspended)
  },
  actions: {
    applyModelConfig(config?: RuntimeModelConfig): void {
      if (!config) return

      const modelOptions = uniqueModelOptions([
        config.default_model,
        ...(Array.isArray(config.model_options) ? config.model_options : [])
      ])
      const defaultModel = normalizeModelOption(config.default_model)

      this.modelOptions = modelOptions
      if (
        !modelOptions.includes(this.selectedModel) ||
        (this.selectedModel === FALLBACK_MODEL_OPTIONS[0] && defaultModel)
      ) {
        this.selectedModel =
          defaultModel && modelOptions.includes(defaultModel) ? defaultModel : modelOptions[0]
      }

      const reasoningOptions = normalizeReasoningOptions(
        Array.isArray(config.reasoning_effort_options) ? config.reasoning_effort_options : []
      )
      const defaultReasoning = normalizeReasoningEffort(config.reasoning_effort) || 'off'

      this.reasoningEffortOptions = reasoningOptions
      if (
        !reasoningOptions.includes(this.reasoningEffort) ||
        (this.reasoningEffort === 'off' && defaultReasoning !== 'off')
      ) {
        this.reasoningEffort = reasoningOptions.includes(defaultReasoning)
          ? defaultReasoning
          : 'off'
      }
    },

    setSelectedModel(model: string): void {
      const normalized = normalizeModelOption(model)
      if (!normalized || !this.modelOptions.includes(normalized)) return
      this.selectedModel = normalized
    },

    setReasoningEffort(effort: string): void {
      const normalized = normalizeReasoningEffort(effort)
      if (!normalized || !this.reasoningEffortOptions.includes(normalized)) return
      this.reasoningEffort = normalized
    },

    async connect(options: { silent?: boolean } = {}): Promise<void> {
      if (this.connectionStatus === 'connected' || this.connectionStatus === 'connecting') return

      manualDisconnect = false
      clearReconnectTimer()
      this.connectionStatus = 'connecting'
      this.errorMessage = ''

      runtimeSocket = new RuntimeSocket(this.endpoint, {
        onOpen: () => {
          clearReconnectTimer()
          this.connectionStatus = 'connected'
        },
        onClose: () => {
          this.connectionStatus = 'disconnected'
          if (!manualDisconnect) {
            this.scheduleReconnect()
          }
        },
        onError: (error) => {
          this.connectionStatus = 'error'
          this.errorMessage = error.message
        },
        onEvent: (event) => this.handleEvent(event)
      })

      try {
        await runtimeSocket.connect()
      } catch {
        if (!options.silent) {
          useChatStore().addSystemMessage(`无法连接后端：${this.endpoint}，正在后台重试。`)
        }
        this.scheduleReconnect()
      }
    },

    disconnect(): void {
      manualDisconnect = true
      clearReconnectTimer()
      runtimeSocket?.disconnect()
      runtimeSocket = null
      this.connectionStatus = 'disconnected'
    },

    scheduleReconnect(): void {
      if (manualDisconnect || reconnectTimer) return
      if (this.connectionStatus === 'connected' || this.connectionStatus === 'connecting') return

      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        void this.connect({ silent: true })
      }, RECONNECT_DELAY_MS)
    },

    persistWorkspaceProjects(): void {
      try {
        window.localStorage.setItem(
          WORKSPACE_PROJECTS_STORAGE_KEY,
          JSON.stringify(this.workspaceProjects)
        )
      } catch {
        // localStorage is a convenience cache; runtime behavior should not depend on it.
      }
    },

    persistConversationWorkspaces(): void {
      try {
        window.localStorage.setItem(
          CONVERSATION_WORKSPACES_STORAGE_KEY,
          JSON.stringify(this.conversationSelectedRoots)
        )
      } catch {
        // Conversation roots are rediscovered when a new-chat workspace is opened.
      }
    },

    isConversationWorkspace(root: string): boolean {
      const normalizedRoot = normalizeSelectedRoot(root)
      return (
        isDefaultConversationSelectedRoot(normalizedRoot) ||
        this.conversationSelectedRoots.includes(normalizedRoot) ||
        this.pendingConversationSelectedRoot === normalizedRoot
      )
    },

    rememberConversationWorkspace(root: string): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      if (!normalizedRoot) return

      this.conversationSelectedRoots = [
        normalizedRoot,
        ...this.conversationSelectedRoots.filter((item) => item !== normalizedRoot)
      ].slice(0, MAX_CONVERSATION_WORKSPACES)
      this.workspaceProjects = this.workspaceProjects.filter(
        (item) => normalizeSelectedRoot(item.selected_root) !== normalizedRoot
      )
      this.persistConversationWorkspaces()
      this.persistWorkspaceProjects()
    },

    rememberWorkspace(workspace?: RuntimeWorkspace | null): void {
      if (!workspace) return

      const selectedRoot = normalizeSelectedRoot(workspace.selected_root)
      if (this.isConversationWorkspace(selectedRoot)) {
        this.rememberConversationWorkspace(selectedRoot)
        return
      }

      const project: RuntimeWorkspaceProject = {
        ...workspace,
        selected_root: selectedRoot,
        updated_at: Date.now()
      }
      this.workspaceProjects = [
        project,
        ...this.workspaceProjects.filter((item) => item.selected_root !== selectedRoot)
      ].slice(0, MAX_WORKSPACE_PROJECTS)
      this.persistWorkspaceProjects()
    },

    cacheWorkspaceSessions(root: string, sessions: RuntimeSessionSummary[]): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      if (!normalizedRoot) return

      this.sessionsBySelectedRoot = {
        ...this.sessionsBySelectedRoot,
        [normalizedRoot]: sessions
      }

      try {
        window.localStorage.setItem(
          WORKSPACE_SESSIONS_STORAGE_KEY,
          JSON.stringify(this.sessionsBySelectedRoot)
        )
      } catch {
        // Session summaries are refreshed from the Python runtime when available.
      }
    },

    removeCachedSession(root: string, sessionId: string): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      if (!normalizedRoot) return

      const cachedSessions = this.sessionsBySelectedRoot[normalizedRoot] || []
      this.sessionsBySelectedRoot = {
        ...this.sessionsBySelectedRoot,
        [normalizedRoot]: cachedSessions.filter((session) => session.session_id !== sessionId)
      }
      if (this.workspace?.selected_root && normalizeSelectedRoot(this.workspace.selected_root) === normalizedRoot) {
        this.sessionHistory = this.sessionHistory.filter(
          (session) => session.session_id !== sessionId
        )
      }

      try {
        window.localStorage.setItem(
          WORKSPACE_SESSIONS_STORAGE_KEY,
          JSON.stringify(this.sessionsBySelectedRoot)
        )
      } catch {
        // The backend is the source of truth; cache cleanup is best-effort.
      }
    },

    async sendUserInput(content: string): Promise<void> {
      const text = content.trim()
      if (!text) return

      const chat = useChatStore()

      if (!runtimeSocket?.isOpen) {
        await this.connect()
      }

      if (!runtimeSocket?.isOpen) {
        chat.addSystemMessage('后端还没有连接，先启动 Python WebSocket 服务后再发送。')
        return
      }

      chat.addUserMessage(text)
      chat.setFallbackConversationTitle()
      runtimeSocket.send({
        type: 'user_input',
        content: text,
        model: this.selectedModel,
        reasoning_effort: this.reasoningEffort
      })
    },

    async deleteSessionInWorkspace(path: string, sessionId: string): Promise<void> {
      const root = normalizeSelectedRoot(path)
      if (!sessionId) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法删除会话。'
        return
      }

      runtimeSocket.send({
        type: 'delete_session',
        request_id: `delete-session-${Date.now()}`,
        session_id: sessionId,
        workspace_path: root
      })
    },

    approvePermission(): void {
      this.sendPermissionDecision(true)
    },

    denyPermission(feedback?: string): void {
      this.sendPermissionDecision(false, feedback)
    },

    answerClarification(payload: ClarificationResponsePayload): void {
      this.sendClarificationResponse(payload)
    },

    skipClarification(): void {
      this.sendClarificationResponse({ skipped: true })
    },

    async resumeSession(sessionId?: string): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) return

      if (sessionId) {
        this.selectedSessionId = sessionId
      }

      runtimeSocket?.send({
        type: 'resume_session',
        session_id: sessionId
      })
    },

    requestSessions(limit = 30): void {
      if (!runtimeSocket?.isOpen) return

      this.sessionsLoading = true
      runtimeSocket.send({
        type: 'list_sessions',
        request_id: `sessions-${Date.now()}`,
        limit
      })
    },

    async startNewConversation(): Promise<void> {
      const chat = useChatStore()
      chat.resetConversation()
      this.activeTurnId = ''
      this.activePermission = null
      this.activeClarification = null
      this.errorMessage = ''

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.sessionState = null
        return
      }

      runtimeSocket.send({
        type: 'new_session',
        request_id: `new-session-${Date.now()}`
      })
      this.requestSessions()
    },

    async startNewConversationInWorkspace(path: string): Promise<void> {
      const root = normalizeSelectedRoot(path)
      if (this.workspace?.selected_root && normalizeSelectedRoot(this.workspace.selected_root) === root) {
        await this.startNewConversation()
        return
      }

      await this.openWorkspace(path)
    },

    async openConversationWorkspace(path: string): Promise<void> {
      const root = normalizeSelectedRoot(path)
      this.pendingConversationSelectedRoot = root
      this.rememberConversationWorkspace(root)
      await this.openWorkspace(path)
    },

    async resumeSessionInWorkspace(path: string, sessionId: string): Promise<void> {
      const root = normalizeSelectedRoot(path)
      if (this.workspace?.selected_root && normalizeSelectedRoot(this.workspace.selected_root) === root) {
        await this.resumeSession(sessionId)
        return
      }

      this.pendingWorkspaceResume = { selected_root: root, sessionId }
      await this.openWorkspace(path)
    },

    async openWorkspace(path: string): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.pendingWorkspaceResume = null
        this.pendingConversationSelectedRoot = null
        this.errorMessage = '后端还没有连接，无法打开工作区。'
        return
      }

      runtimeSocket.send({
        type: 'open_workspace',
        request_id: `workspace-${Date.now()}`,
        path
      })
    },

    sendPermissionDecision(approved: boolean, feedback?: string): void {
      if (!this.activePermission || !runtimeSocket?.isOpen) return

      runtimeSocket.send({
        type: 'permission_decision',
        request_id: this.activePermission.request_id,
        approved,
        feedback
      })
    },

    sendClarificationResponse(payload: ClarificationResponsePayload): void {
      if (!this.activeClarification || !runtimeSocket?.isOpen) return

      runtimeSocket.send({
        type: 'clarification_response',
        request_id: this.activeClarification.request_id,
        ...payload
      })
    },

    requestConversationTitle(): void {
      const chat = useChatStore()
      if (!runtimeSocket?.isOpen || !chat.needsConversationTitle) return

      const messages = chat.getConversationTitleMessages()
      if (!messages.length) return

      const requestId = `title-${Date.now()}`
      chat.startConversationTitleRequest(requestId)
      runtimeSocket.send({
        type: 'conversation_title_request',
        request_id: requestId,
        messages
      })
    },

    handleEvent(event: RuntimeEvent): void {
      const chat = useChatStore()
      this.events.push(event)
      if (this.events.length > MAX_EVENTS) this.events.shift()

      switch (event.type) {
        case 'ready':
          this.sessionId = event.session_id
          this.selectedSessionId = event.session_id
          this.schemaVersion = event.schema_version
          this.tools = event.tools
          this.sessionState = event.session_state
          this.workspace = event.workspace || null
          this.applyModelConfig(event.model_config)
          this.rememberWorkspace(this.workspace)
          if (this.workspace?.selected_root) {
            this.sessionHistory =
              this.sessionsBySelectedRoot[normalizeSelectedRoot(this.workspace.selected_root)] || []
          }
          chat.addSystemMessage(`已连接后端：${event.session_id}`)
          this.requestSessions()
          this.requestConversationTitle()
          break
        case 'workspace_changed':
          this.sessionId = event.session_id
          this.selectedSessionId = event.session_id
          this.sessionState = event.session_state
          this.workspace = event.workspace
          this.rememberWorkspace(event.workspace)
          this.tools = event.tools
          this.activeTurnId = ''
          this.activePermission = null
          this.activeClarification = null
          this.sessionHistory =
            this.sessionsBySelectedRoot[normalizeSelectedRoot(event.workspace.selected_root)] || []
          chat.resetConversation()
          chat.addSystemMessage(`已打开工作区：${event.workspace.display_name}`)
          this.requestSessions()
          if (
            this.pendingConversationSelectedRoot === normalizeSelectedRoot(event.workspace.selected_root)
          ) {
            this.pendingConversationSelectedRoot = null
          }
          if (this.pendingWorkspaceResume?.selected_root === normalizeSelectedRoot(event.workspace.selected_root)) {
            const sessionId = this.pendingWorkspaceResume.sessionId
            this.pendingWorkspaceResume = null
            void this.resumeSession(sessionId)
          }
          break
        case 'turn_started':
          this.activeTurnId = event.turn_id
          this.sessionState = event.session_state
          chat.startActivity()
          break
        case 'session_created':
          this.sessionId = event.session_id
          this.selectedSessionId = event.session_id
          this.sessionState = event.session_state
          this.workspace = event.workspace || this.workspace
          this.rememberWorkspace(this.workspace)
          if (this.workspace?.selected_root) {
            this.sessionHistory =
              this.sessionsBySelectedRoot[normalizeSelectedRoot(this.workspace.selected_root)] || []
          }
          this.activeTurnId = ''
          this.activePermission = null
          this.activeClarification = null
          chat.resetConversation()
          this.requestSessions()
          break
        case 'sessions_list':
          if (
            !event.workspace?.selected_root ||
            normalizeSelectedRoot(event.workspace.selected_root) ===
              (this.workspace?.selected_root ? normalizeSelectedRoot(this.workspace.selected_root) : '')
          ) {
            this.sessionHistory = event.sessions
          }
          if (event.workspace?.selected_root) {
            this.cacheWorkspaceSessions(event.workspace.selected_root, event.sessions)
          } else if (this.workspace?.selected_root) {
            this.cacheWorkspaceSessions(this.workspace.selected_root, event.sessions)
          }
          this.sessionsLoading = false
          break
        case 'session_deleted':
          if (event.workspace?.selected_root) {
            this.cacheWorkspaceSessions(event.workspace.selected_root, event.sessions)
            if (
              normalizeSelectedRoot(event.workspace.selected_root) ===
              (this.workspace?.selected_root ? normalizeSelectedRoot(this.workspace.selected_root) : '')
            ) {
              this.sessionHistory = event.sessions
            }
          }
          if (event.deleted_current) {
            this.sessionId = event.session_id || this.sessionId
            this.selectedSessionId = event.session_id || this.selectedSessionId
            this.sessionState = event.session_state
            this.activeTurnId = ''
            this.activePermission = null
            this.activeClarification = null
            chat.resetConversation()
          } else if (this.selectedSessionId === event.deleted_session_id) {
            this.selectedSessionId = this.sessionId
          }
          break
        case 'assistant_token':
          chat.appendAssistantToken(event.token)
          break
        case 'tool_call_started':
          chat.upsertActivityEvent(formatToolStartLabel(event.name, event.arguments), 'running', {
            requestId: event.request_id,
            kind: getToolKind(event.name),
            detail: formatToolDetail(event.arguments)
          })
          break
        case 'tool_call_result':
          chat.upsertActivityEvent(
            formatToolResultLabel(event.name, event.ok),
            event.ok ? 'success' : 'error',
            {
              requestId: event.request_id,
              kind: getToolKind(event.name),
              detail: event.content
            }
          )
          break
        case 'permission_request':
          this.activePermission = event
          this.activeClarification = null
          chat.upsertActivityEvent(`等待权限确认：${formatToolLabel(event)}`, 'waiting', {
            requestId: event.request_id,
            kind: 'permission',
            detail: event.detail
          })
          break
        case 'permission_decision_ack':
          this.activePermission = null
          chat.upsertActivityEvent(
            event.approved ? '权限已允许' : '权限已拒绝',
            event.approved ? 'success' : 'error',
            {
              requestId: event.request_id,
              kind: 'permission'
            }
          )
          break
        case 'clarification_request':
          this.activeClarification = event
          this.activePermission = null
          chat.upsertActivityEvent(`正在询问：${formatClarificationLabel(event)}`, 'waiting', {
            requestId: event.request_id,
            kind: 'question',
            detail: formatClarificationDetail(event)
          })
          break
        case 'clarification_response_ack':
          this.activeClarification = null
          chat.upsertActivityEvent(event.skipped ? '已跳过问题' : '已收到回答', 'success', {
            requestId: event.request_id,
            kind: 'question'
          })
          break
        case 'session_suspended':
        case 'session_blocked':
          this.sessionState = event.session_state
          chat.addSystemMessage(event.detail || event.type)
          break
        case 'session_resumed':
          if (event.session_id) {
            this.sessionId = event.session_id
            this.selectedSessionId = event.session_id
          }
          this.sessionState = event.session_state
          this.activeClarification = null
          if (event.resumed_from_disk) {
            chat.loadConversation(event.messages || [], event.session?.title || '历史会话')
          } else {
            chat.addSystemMessage(event.detail || '会话已恢复。')
          }
          this.requestSessions()
          this.requestConversationTitle()
          break
        case 'final_answer':
          this.sessionState = event.session_state
          this.activeTurnId = ''
          this.activeClarification = null
          chat.finishActivity('success')
          chat.finishAssistantStream(event.content)
          this.requestSessions()
          this.requestConversationTitle()
          break
        case 'conversation_title':
          chat.finishConversationTitleRequest(event.title, event.request_id)
          this.requestSessions()
          break
        case 'error':
        case 'workspace_error':
          this.errorMessage = event.message
          if (event.type === 'workspace_error') {
            this.pendingWorkspaceResume = null
            this.pendingConversationSelectedRoot = null
          }
          if (event.request_id?.startsWith('sessions-')) {
            this.sessionsLoading = false
          }
          if (event.request_id?.startsWith('title-')) {
            chat.failConversationTitleRequest(event.request_id)
            break
          }
          chat.upsertActivityEvent('后端错误', 'error', {
            requestId: event.request_id,
            kind: 'error',
            detail: event.message
          })
          chat.finishActivity('error')
          break
      }
    }
  }
})
