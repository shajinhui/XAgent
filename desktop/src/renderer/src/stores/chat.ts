import { defineStore } from 'pinia'

export type ChatRole = 'user' | 'assistant' | 'system'

export type ChatMessage = {
  id: number
  role: ChatRole
  content: string
  meta?: string
}

let nextMessageId = Date.now()

function createMessageId(): number {
  nextMessageId += 1
  return nextMessageId
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    streamingMessageId: null as number | null,
    messages: [
      {
        id: createMessageId(),
        role: 'assistant',
        meta: '桌面端',
        content: '聊天壳已经接入前端 WebSocket 层。启动后端后，可以直接从这里发送消息。'
      }
    ] as ChatMessage[]
  }),
  getters: {
    messageCount: (state) => state.messages.length
  },
  actions: {
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

    startAssistantStream(meta = '运行中'): void {
      if (this.streamingMessageId) return

      const id = createMessageId()
      this.streamingMessageId = id
      this.messages.push({
        id,
        role: 'assistant',
        content: '',
        meta
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
        if (text) this.addAssistantMessage(text, '完成')
        return
      }

      const message = this.messages.find((item) => item.id === this.streamingMessageId)
      if (message) {
        message.content = text || message.content || '完成。'
        message.meta = '完成'
      }
      this.streamingMessageId = null
    }
  }
})
