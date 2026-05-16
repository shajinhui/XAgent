import { defineStore } from 'pinia'
import type { ConversationTitleMessage, RuntimeDisplayMessage } from '@renderer/types/runtimeEvents'

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
  requestId?: string
}

export type ChatMessage = {
  id: number
  role: ChatRole
  content: string
  meta?: string
  collapsed?: boolean
  startedAt?: number
  finishedAt?: number
  step?: ActivityStep
  activityGroupId?: number
  isFinal?: boolean
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

function createFallbackConversationTitle(messages: ConversationTitleMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === 'user')
  const title = firstUserMessage?.content.replace(/\s+/g, ' ').trim()

  if (!title) return '新对话'
  if (title.length <= 18) return title
  return `${title.slice(0, 18)}...`
}

function createWelcomeMessage(): ChatMessage {
  return {
    id: createMessageId(),
    role: 'assistant',
    meta: '桌面端',
    content: '聊天壳已经接入前端 WebSocket 层。启动后端后，可以直接从这里发送消息。'
  }
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversationTitle: '新对话',
    conversationTitleGenerated: false,
    conversationTitleRequestId: '',
    conversationTitleStatus: 'idle' as 'idle' | 'pending' | 'ready' | 'error',
    streamingMessageId: null as number | null,
    activeActivityMessageId: null as number | null,
    messages: [createWelcomeMessage()] as ChatMessage[]
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
      this.messages = [createWelcomeMessage()]
    },

    loadConversation(messages: RuntimeDisplayMessage[], title: string): void {
      const cleanTitle = title.trim() || '历史会话'
      this.conversationTitle = cleanTitle
      this.conversationTitleGenerated = cleanTitle !== '新对话'
      this.conversationTitleRequestId = ''
      this.conversationTitleStatus = cleanTitle === '新对话' ? 'idle' : 'ready'
      this.streamingMessageId = null
      this.activeActivityMessageId = null
      this.messages = messages.length
        ? messages.map((message) => ({
            id: createMessageId(),
            role: message.role,
            content: message.content,
            isFinal: message.role === 'assistant'
          }))
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

    startActivity(): void {
      if (this.activeActivityMessageId) return

      const id = createMessageId()
      this.activeActivityMessageId = id
      this.messages.push({
        id,
        role: 'activity',
        content: '正在思考',
        collapsed: false,
        startedAt: Date.now()
      })
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
      options: { detail?: string; requestId?: string; kind?: ActivityStepKind } = {}
    ): void {
      if (!this.activeActivityMessageId) {
        this.startActivity()
      }

      const message = this.messages.find((item) => item.id === this.activeActivityMessageId)
      if (!message) return

      const events = this.messages.filter(
        (item) => item.role === 'activity_event' && item.activityGroupId === message.id
      )
      const existing = options.requestId
        ? events.find((item) => item.step?.requestId === options.requestId)
        : undefined
      const kind = options.kind || existing?.step?.kind || 'tool'
      message.content = getActivitySummary(kind, status)

      if (existing) {
        existing.content = label
        existing.step = {
          id: existing.step?.id || createMessageId(),
          label,
          status,
          kind,
          detail: options.detail,
          requestId: options.requestId
        }
        return
      }

      this.finishAssistantSegment()
      this.messages.push({
        id: createMessageId(),
        role: 'activity_event',
        content: label,
        activityGroupId: message.id,
        step: {
          id: createMessageId(),
          label,
          status,
          kind,
          detail: options.detail,
          requestId: options.requestId
        }
      })
    },

    finishActivity(status: 'success' | 'error' = 'success'): void {
      if (!this.activeActivityMessageId) return

      const message = this.messages.find((item) => item.id === this.activeActivityMessageId)
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

      this.activeActivityMessageId = null
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

    finishAssistantStream(content: string): void {
      const text = content.trim()
      if (!this.streamingMessageId) {
        if (text) {
          this.messages.push({
            id: createMessageId(),
            role: 'assistant',
            content: text,
            isFinal: true
          })
        }
        return
      }

      const message = this.messages.find((item) => item.id === this.streamingMessageId)
      if (message) {
        message.content = text || message.content || '完成。'
        message.meta = undefined
        message.isFinal = true
      }
      this.streamingMessageId = null
    }
  }
})
