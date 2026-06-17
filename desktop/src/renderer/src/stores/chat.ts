import { defineStore } from 'pinia'
import type {
  ConversationTitleMessage,
  RuntimeDisplayMessage,
  RuntimeTaskListItem
} from '@renderer/types/runtimeEvents'

export type ChatRole = 'user' | 'assistant' | 'system' | 'activity' | 'activity_event'

export type ActivityStepStatus = 'running' | 'waiting' | 'success' | 'error'
export type ActivityStepKind =
  | 'thinking'
  | 'search'
  | 'read'
  | 'edit'
  | 'command'
  | 'permission'
  | 'question'
  | 'web'
  | 'tool'
  | 'error'

export type ActivityStep = {
  id: number
  label: string
  status: ActivityStepStatus
  kind: ActivityStepKind
  detail?: string
  startedLabel?: string
  inputDetail?: string
  requestId?: string
  toolName?: string
}

export type ChatMessage = {
  id: number
  role: ChatRole
  content: string
  meta?: string
  timestamp?: number
  collapsed?: boolean
  startedAt?: number
  finishedAt?: number
  step?: ActivityStep
  activityGroupId?: number
  turnId?: string
  isFinal?: boolean
  changedFiles?: Array<{ path: string; can_undo: boolean; additions?: number; deletions?: number }>
  taskList?: RuntimeTaskListItem[]
}

let nextMessageId = Date.now()

function createMessageId(): number {
  nextMessageId += 1
  return nextMessageId
}

function formatElapsedTime(startedAt?: number, finishedAt?: number): string {
  if (!startedAt) return '0s'

  const elapsedSeconds = Math.max(1, Math.round(((finishedAt || Date.now()) - startedAt) / 1000))
  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60

  if (!minutes) return `${seconds}s`
  return `${minutes}m ${seconds}s`
}

function getActivitySummary(kind: ActivityStepKind, status: ActivityStepStatus): string {
  if (status === 'waiting' && kind === 'question') return '等待用户回复'
  if (status === 'waiting') return '等待权限确认'
  if (status === 'error') return '处理遇到问题'
  if (kind === 'command') return '正在运行命令'
  if (kind === 'permission') return '等待权限确认'
  if (kind === 'question') return '正在询问问题'
  return '正在使用工具'
}

function isProgressToolKind(kind: ActivityStepKind): boolean {
  return !['permission', 'question', 'thinking'].includes(kind)
}

function isPlanningToolName(toolName?: string): boolean {
  return toolName === 'create_task_list' || toolName === 'update_plan'
}

function shouldAdvanceTaskList(kind: ActivityStepKind, toolName?: string): boolean {
  return isProgressToolKind(kind) && !isPlanningToolName(toolName)
}

function ensureTaskListProgress(taskList?: RuntimeTaskListItem[]): void {
  if (!taskList?.length) return
  if (taskList.some((item) => item.status === 'in_progress')) return

  const nextTask = taskList.find((item) => item.status === 'pending')
  if (nextTask) {
    nextTask.status = 'in_progress'
  }
}

function advanceTaskListProgress(
  taskList: RuntimeTaskListItem[] | undefined,
  status: ActivityStepStatus
): void {
  if (!taskList?.length) return

  const activeTask = taskList.find((item) => item.status === 'in_progress')
  if (!activeTask) {
    ensureTaskListProgress(taskList)
    return
  }

  if (status !== 'success' && status !== 'error') return

  activeTask.status = status === 'success' ? 'completed' : 'error'
  if (status === 'error') return

  const nextTask = taskList.find((item) => item.status === 'pending')
  if (nextTask) {
    nextTask.status = 'in_progress'
  }
}

function normalizeRuntimeTaskList(items: RuntimeTaskListItem[]): RuntimeTaskListItem[] {
  return items
    .filter((item) => typeof item.step === 'string' && item.step.trim())
    .slice(0, 5)
    .map((item, index) => {
      const status =
        item.status === 'completed' || item.status === 'error' || item.status === 'in_progress'
          ? item.status
          : index === 0
            ? 'in_progress'
            : 'pending'

      return {
        step: item.step.trim(),
        status
      }
    })
}

function findActivityMessage(
  messages: ChatMessage[],
  activeActivityMessageId: number | null,
  turnId?: string
): ChatMessage | undefined {
  const normalizedTurnId = turnId?.trim()

  if (normalizedTurnId) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (
        message.role === 'activity' &&
        message.turnId === normalizedTurnId &&
        !message.finishedAt
      ) {
        return message
      }
    }
  }

  if (activeActivityMessageId) {
    const activeMessage = messages.find(
      (message) =>
        message.id === activeActivityMessageId && message.role === 'activity' && !message.finishedAt
    )
    if (activeMessage) return activeMessage
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === 'activity' && !message.finishedAt) return message
  }

  return undefined
}

function replayExistingActivityProgress(
  taskList: RuntimeTaskListItem[],
  messages: ChatMessage[],
  activityGroupId: number
): void {
  ensureTaskListProgress(taskList)

  const activityEvents = messages.filter(
    (message) => message.role === 'activity_event' && message.activityGroupId === activityGroupId
  )
  for (const eventMessage of activityEvents) {
    const step = eventMessage.step
    if (!step || !shouldAdvanceTaskList(step.kind, step.toolName)) continue
    if (step.status === 'success' || step.status === 'error') {
      advanceTaskListProgress(taskList, step.status)
    }
  }
}

function createFallbackConversationTitle(messages: ConversationTitleMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === 'user')
  const title = firstUserMessage?.content.replace(/\s+/g, ' ').trim()

  if (!title) return '新对话'
  if (title.length <= 18) return title
  return `${title.slice(0, 18)}...`
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversationTitle: '新对话',
    conversationTitleGenerated: false,
    conversationTitleRequestId: '',
    conversationTitleStatus: 'idle' as 'idle' | 'pending' | 'ready' | 'error',
    streamingMessageId: null as number | null,
    activeActivityMessageId: null as number | null,
    pendingTaskListsByTurn: {} as Record<string, RuntimeTaskListItem[]>,
    messages: [] as ChatMessage[]
  }),
  getters: {
    messageCount: (state) =>
      state.messages.filter((message) => message.role === 'user' || message.role === 'assistant')
        .length,
    needsConversationTitle: (state) =>
      !state.conversationTitleGenerated &&
      state.conversationTitleStatus !== 'pending' &&
      state.messages.some((message) => message.role === 'user') &&
      state.messages.some((message) => message.role === 'assistant')
  },
  actions: {
    resetConversation(): void {
      this.conversationTitle = '新对话'
      this.conversationTitleGenerated = false
      this.conversationTitleRequestId = ''
      this.conversationTitleStatus = 'idle'
      this.streamingMessageId = null
      this.activeActivityMessageId = null
      this.pendingTaskListsByTurn = {}
      this.messages = []
    },

    loadConversation(messages: RuntimeDisplayMessage[], title: string): void {
      const cleanTitle = title.trim() || '历史会话'
      const activityGroupIds = new Map<string, number>()
      const restoredMessages: ChatMessage[] = []

      for (const message of messages) {
        const id = createMessageId()
        const activityKey =
          message.activity_key || (message.role === 'activity' ? `activity:${id}` : '')
        const activityGroupId = activityKey ? activityGroupIds.get(activityKey) : undefined

        if (message.role === 'activity' && activityKey) {
          activityGroupIds.set(activityKey, id)
        }

        const restoredMessage: ChatMessage = {
          id,
          role: message.role,
          content: message.content,
          timestamp: message.timestamp,
          collapsed: message.role === 'activity' ? (message.collapsed ?? true) : message.collapsed,
          startedAt: message.startedAt,
          finishedAt: message.finishedAt,
          activityGroupId,
          isFinal: message.role === 'assistant' ? (message.isFinal ?? true) : message.isFinal
        }

        if (message.role === 'activity_event' && message.step) {
          restoredMessage.step = {
            id: createMessageId(),
            label: message.step.label || message.content,
            status: message.step.status,
            kind: message.step.kind,
            detail: message.step.detail,
            requestId: message.step.requestId,
            toolName: message.step.toolName
          }
        }

        restoredMessages.push(restoredMessage)
      }

      this.conversationTitle = cleanTitle
      this.conversationTitleGenerated = cleanTitle !== '新对话'
      this.conversationTitleRequestId = ''
      this.conversationTitleStatus = cleanTitle === '新对话' ? 'idle' : 'ready'
      this.streamingMessageId = null
      this.activeActivityMessageId = null
      this.pendingTaskListsByTurn = {}
      this.messages = restoredMessages.length
        ? restoredMessages
        : [
            {
              id: createMessageId(),
              role: 'system',
              content: '这个历史会话还没有可展示消息。'
            }
          ]
    },

    getConversationTitleMessages(): ConversationTitleMessage[] {
      const titleMessages: ConversationTitleMessage[] = []
      let hasUserMessage = false

      for (const message of this.messages) {
        if (message.role === 'user') {
          hasUserMessage = true
          titleMessages.push({
            role: 'user',
            content: message.content.slice(0, 1200)
          })
          continue
        }

        if (message.role === 'assistant' && hasUserMessage && message.content.trim()) {
          titleMessages.push({
            role: 'assistant',
            content: message.content.slice(0, 1200)
          })
        }
      }

      return titleMessages.slice(-8)
    },

    startConversationTitleRequest(requestId: string): void {
      this.setFallbackConversationTitle()
      this.conversationTitleRequestId = requestId
      this.conversationTitleStatus = 'pending'
    },

    setFallbackConversationTitle(): void {
      if (this.conversationTitleGenerated) return

      const fallbackTitle = createFallbackConversationTitle(this.getConversationTitleMessages())
      if (fallbackTitle !== '新对话') {
        this.conversationTitle = fallbackTitle
      }
    },

    finishConversationTitleRequest(title: string, requestId?: string): void {
      if (
        requestId &&
        this.conversationTitleRequestId &&
        requestId !== this.conversationTitleRequestId
      ) {
        return
      }

      const text = title.trim()
      if (text) {
        this.conversationTitle = text
        this.conversationTitleGenerated = true
        this.conversationTitleStatus = 'ready'
      }
      this.conversationTitleRequestId = ''
    },

    failConversationTitleRequest(requestId?: string): void {
      if (
        requestId &&
        this.conversationTitleRequestId &&
        requestId !== this.conversationTitleRequestId
      ) {
        return
      }

      this.conversationTitleStatus = 'error'
      this.conversationTitleRequestId = ''
    },

    addUserMessage(content: string): void {
      const text = content.trim()
      if (!text) return

      this.messages.push({
        id: createMessageId(),
        role: 'user',
        content: text
      })
    },

    addAssistantMessage(content: string, meta?: string): void {
      this.messages.push({
        id: createMessageId(),
        role: 'assistant',
        content,
        meta
      })
    },

    addSystemMessage(content: string): void {
      this.messages.push({
        id: createMessageId(),
        role: 'system',
        content
      })
    },

    startActivity(turnId?: string): void {
      const activity = findActivityMessage(this.messages, this.activeActivityMessageId, turnId)
      if (activity) {
        if (turnId && !activity.turnId) {
          activity.turnId = turnId
        }
        this.activeActivityMessageId = activity.id
        if (turnId && this.pendingTaskListsByTurn[turnId]?.length) {
          this.setActiveTaskList(this.pendingTaskListsByTurn[turnId], turnId)
        }
        return
      }

      const id = createMessageId()
      this.activeActivityMessageId = id
      this.messages.push({
        id,
        role: 'activity',
        content: '正在思考',
        collapsed: false,
        startedAt: Date.now(),
        turnId: turnId || undefined
      })

      if (turnId && this.pendingTaskListsByTurn[turnId]?.length) {
        this.setActiveTaskList(this.pendingTaskListsByTurn[turnId], turnId)
      }
    },

    setActiveTaskList(items: RuntimeTaskListItem[], turnId?: string): void {
      const normalizedItems = normalizeRuntimeTaskList(items)
      if (!normalizedItems.length) return

      let message = findActivityMessage(this.messages, this.activeActivityMessageId, turnId)
      if (!message) {
        if (turnId) {
          this.pendingTaskListsByTurn[turnId] = normalizedItems
        }
        this.startActivity(turnId)
        message = findActivityMessage(this.messages, this.activeActivityMessageId, turnId)
      }
      if (!message) return

      if (turnId && !message.turnId) {
        message.turnId = turnId
      }
      message.taskList = normalizedItems
      replayExistingActivityProgress(message.taskList, this.messages, message.id)
      this.activeActivityMessageId = message.id
      if (turnId) {
        delete this.pendingTaskListsByTurn[turnId]
      }
    },

    finishAssistantSegment(): void {
      if (!this.streamingMessageId) return

      const message = this.messages.find((item) => item.id === this.streamingMessageId)
      if (message && !message.content.trim()) {
        this.messages = this.messages.filter((item) => item.id !== this.streamingMessageId)
      }
      this.streamingMessageId = null
    },

    upsertActivityEvent(
      label: string,
      status: ActivityStepStatus,
      options: {
        detail?: string
        requestId?: string
        kind?: ActivityStepKind
        toolName?: string
        turnId?: string
      } = {}
    ): void {
      let message = findActivityMessage(this.messages, this.activeActivityMessageId, options.turnId)
      if (!message) {
        this.startActivity(options.turnId)
        message = findActivityMessage(this.messages, this.activeActivityMessageId, options.turnId)
      }
      if (!message) return
      if (options.turnId && !message.turnId) {
        message.turnId = options.turnId
      }
      this.activeActivityMessageId = message.id

      const events = this.messages.filter(
        (item) => item.role === 'activity_event' && item.activityGroupId === message.id
      )
      const existing = options.requestId
        ? events.find((item) => item.step?.requestId === options.requestId)
        : undefined
      const kind = options.kind || existing?.step?.kind || 'tool'
      message.content = getActivitySummary(kind, status)
      if (shouldAdvanceTaskList(kind, options.toolName || existing?.step?.toolName)) {
        ensureTaskListProgress(message.taskList)
      }

      if (existing) {
        existing.content = label
        if (status === 'success' || status === 'error') {
          existing.finishedAt = Date.now()
        }
        existing.step = {
          id: existing.step?.id || createMessageId(),
          label,
          status,
          kind,
          detail: options.detail,
          startedLabel: existing.step?.startedLabel || existing.step?.label || existing.content,
          inputDetail: existing.step?.inputDetail || existing.step?.detail || options.detail,
          requestId: options.requestId,
          toolName: options.toolName || existing.step?.toolName
        }
        if (shouldAdvanceTaskList(kind, existing.step.toolName)) {
          advanceTaskListProgress(message.taskList, status)
        }
        return
      }

      this.finishAssistantSegment()
      this.messages.push({
        id: createMessageId(),
        role: 'activity_event',
        content: label,
        activityGroupId: message.id,
        startedAt: Date.now(),
        step: {
          id: createMessageId(),
          label,
          status,
          kind,
          detail: options.detail,
          startedLabel: label,
          inputDetail: options.detail,
          requestId: options.requestId,
          toolName: options.toolName
        }
      })
      if (shouldAdvanceTaskList(kind, options.toolName)) {
        advanceTaskListProgress(message.taskList, status)
      }
    },

    finishActivity(status: 'success' | 'error' = 'success', turnId?: string): void {
      const message = findActivityMessage(this.messages, this.activeActivityMessageId, turnId)
      if (!message) return

      const eventMessages = this.messages.filter(
        (item) => item.role === 'activity_event' && item.activityGroupId === message.id
      )
      const errorCount = eventMessages.filter((item) => item.step?.status === 'error').length
      const finishedAt = Date.now()

      message.finishedAt = finishedAt
      message.collapsed = true
      message.content =
        status === 'error'
          ? `处理出错 · ${errorCount || 1} 个错误`
          : `已处理 ${formatElapsedTime(message.startedAt, finishedAt)}`

      for (const eventMessage of eventMessages) {
        if (
          eventMessage.step &&
          (eventMessage.step.status === 'running' || eventMessage.step.status === 'waiting')
        ) {
          eventMessage.step.status = status
        }
      }
      if (message.taskList?.length) {
        message.taskList.forEach((task) => {
          if (task.status === 'in_progress') {
            task.status = status === 'success' ? 'completed' : 'error'
          }
        })
      }

      if (this.activeActivityMessageId === message.id) {
        this.activeActivityMessageId = null
      }
      if (turnId) {
        delete this.pendingTaskListsByTurn[turnId]
      }
    },

    toggleActivity(messageId: number): void {
      const message = this.messages.find((item) => item.id === messageId)
      if (message?.role === 'activity') {
        message.collapsed = !message.collapsed
      }
    },

    startAssistantStream(meta?: string): void {
      if (this.streamingMessageId) return

      const id = createMessageId()
      this.streamingMessageId = id
      this.messages.push({
        id,
        role: 'assistant',
        content: '',
        meta,
        activityGroupId: this.activeActivityMessageId || undefined
      })
    },

    appendAssistantToken(token: string): void {
      if (!this.streamingMessageId) {
        this.startAssistantStream()
      }

      const message = this.messages.find((item) => item.id === this.streamingMessageId)
      if (message) {
        message.content += token
      }
    },

    finishAssistantStream(
      content: string,
      changedFiles?: Array<{
        path: string
        can_undo: boolean
        additions?: number
        deletions?: number
      }>
    ): void {
      const text = content.trim()
      if (!this.streamingMessageId) {
        if (text) {
          this.messages.push({
            id: createMessageId(),
            role: 'assistant',
            content: text,
            isFinal: true,
            changedFiles
          })
        }
        return
      }

      const message = this.messages.find((item) => item.id === this.streamingMessageId)
      if (message) {
        message.content = text || message.content || '完成。'
        message.meta = undefined
        message.isFinal = true
        message.changedFiles = changedFiles
      }
      this.streamingMessageId = null
    },

    markChangedFileUndone(filePath: string): void {
      for (const message of this.messages) {
        if (!message.changedFiles?.length) continue

        for (const file of message.changedFiles) {
          if (file.path === filePath) {
            file.can_undo = false
          }
        }
      }
    }
  }
})
