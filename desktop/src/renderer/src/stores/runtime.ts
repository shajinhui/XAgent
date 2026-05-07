import { defineStore } from 'pinia'
import { RuntimeSocket, type RuntimeConnectionStatus } from '@renderer/services/runtimeSocket'
import { useChatStore } from '@renderer/stores/chat'
import type {
  PermissionRequestEvent,
  RuntimeEvent,
  RuntimeSessionState,
  RuntimeToolMetadata
} from '@renderer/types/runtimeEvents'

const DEFAULT_ENDPOINT = import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8000/agent/ws'
const MAX_EVENTS = 120

let runtimeSocket: RuntimeSocket | null = null

function formatToolLabel(event: PermissionRequestEvent): string {
  return event.tool || event.request_id || 'tool'
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
    tools: [] as RuntimeToolMetadata[],
    events: [] as RuntimeEvent[]
  }),
  getters: {
    isConnected: (state) => state.connectionStatus === 'connected',
    isConnecting: (state) => state.connectionStatus === 'connecting',
    isSuspended: (state) => Boolean(state.sessionState?.suspended)
  },
  actions: {
    async connect(): Promise<void> {
      if (this.connectionStatus === 'connected' || this.connectionStatus === 'connecting') return

      this.connectionStatus = 'connecting'
      this.errorMessage = ''

      runtimeSocket = new RuntimeSocket(this.endpoint, {
        onOpen: () => {
          this.connectionStatus = 'connected'
        },
        onClose: () => {
          this.connectionStatus = 'disconnected'
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
        useChatStore().addSystemMessage(`无法连接后端：${this.endpoint}`)
      }
    },

    disconnect(): void {
      runtimeSocket?.disconnect()
      runtimeSocket = null
      this.connectionStatus = 'disconnected'
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
      runtimeSocket.send({
        type: 'user_input',
        content: text
      })
    },

    approvePermission(): void {
      this.sendPermissionDecision(true)
    },

    denyPermission(): void {
      this.sendPermissionDecision(false)
    },

    resumeSession(): void {
      runtimeSocket?.send({
        type: 'resume_session'
      })
    },

    sendPermissionDecision(approved: boolean): void {
      if (!this.activePermission || !runtimeSocket?.isOpen) return

      runtimeSocket.send({
        type: 'permission_decision',
        request_id: this.activePermission.request_id,
        approved
      })
    },

    handleEvent(event: RuntimeEvent): void {
      const chat = useChatStore()
      this.events.push(event)
      if (this.events.length > MAX_EVENTS) this.events.shift()

      switch (event.type) {
        case 'ready':
          this.sessionId = event.session_id
          this.schemaVersion = event.schema_version
          this.tools = event.tools
          this.sessionState = event.session_state
          chat.addSystemMessage(`已连接后端：${event.session_id}`)
          break
        case 'turn_started':
          this.activeTurnId = event.turn_id
          this.sessionState = event.session_state
          chat.startAssistantStream('思考中')
          break
        case 'assistant_token':
          chat.appendAssistantToken(event.token)
          break
        case 'tool_call_started':
          chat.addSystemMessage(`调用工具：${event.name}`)
          break
        case 'tool_call_result':
          chat.addSystemMessage(event.ok ? `工具完成：${event.name}` : `工具失败：${event.name}`)
          break
        case 'permission_request':
          this.activePermission = event
          chat.addSystemMessage(`等待权限确认：${formatToolLabel(event)}`)
          break
        case 'permission_decision_ack':
          this.activePermission = null
          chat.addSystemMessage(event.approved ? '权限已允许。' : '权限已拒绝。')
          break
        case 'session_suspended':
        case 'session_blocked':
        case 'session_resumed':
          this.sessionState = event.session_state
          chat.addSystemMessage(event.detail || event.type)
          break
        case 'final_answer':
          this.sessionState = event.session_state
          this.activeTurnId = ''
          chat.finishAssistantStream(event.content)
          break
        case 'error':
          this.errorMessage = event.message
          chat.addSystemMessage(`后端错误：${event.message}`)
          break
      }
    }
  }
})
