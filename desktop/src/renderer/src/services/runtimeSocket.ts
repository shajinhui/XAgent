import type { RuntimeClientPacket, RuntimeEvent } from '@renderer/types/runtimeEvents'

export type RuntimeConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'

type RuntimeSocketCallbacks = {
  onOpen?: () => void
  onClose?: () => void
  onError?: (error: Error) => void
  onEvent?: (event: RuntimeEvent) => void
}

export class RuntimeSocket {
  private socket: WebSocket | null = null
  private connectPromise: Promise<void> | null = null

  constructor(
    private readonly url: string,
    private readonly callbacks: RuntimeSocketCallbacks
  ) {}

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN
  }

  connect(): Promise<void> {
    if (this.isOpen) return Promise.resolve()
    if (this.connectPromise) return this.connectPromise

    const ws = new WebSocket(this.url)
    this.socket = ws

    this.connectPromise = new Promise((resolve, reject) => {
      let settled = false
      let opened = false

      const settleResolve = (): void => {
        if (settled) return
        settled = true
        opened = true
        this.connectPromise = null
        this.callbacks.onOpen?.()
        resolve()
      }

      const settleReject = (error: Error): void => {
        if (settled) return
        settled = true
        this.connectPromise = null
        this.callbacks.onError?.(error)
        reject(error)
      }

      ws.addEventListener('open', settleResolve)
      ws.addEventListener('message', (message) => this.handleMessage(message))
      ws.addEventListener('error', () =>
        settleReject(new Error('Runtime WebSocket connection failed'))
      )
      ws.addEventListener('close', () => {
        this.connectPromise = null
        if (!opened) {
          settleReject(new Error('Runtime WebSocket closed before it opened'))
          return
        }
        this.callbacks.onClose?.()
      })
    })

    return this.connectPromise
  }

  disconnect(): void {
    this.socket?.close()
    this.socket = null
    this.connectPromise = null
  }

  send(packet: RuntimeClientPacket): void {
    if (!this.isOpen) {
      throw new Error('Runtime WebSocket is not connected')
    }

    this.socket?.send(JSON.stringify(packet))
  }

  private handleMessage(message: MessageEvent<string>): void {
    try {
      const event = JSON.parse(message.data) as RuntimeEvent
      this.callbacks.onEvent?.(event)
    } catch {
      this.callbacks.onError?.(new Error('Runtime WebSocket returned invalid JSON'))
    }
  }
}
