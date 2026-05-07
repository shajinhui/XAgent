export type RuntimeSessionState = {
  status: 'active' | 'suspended'
  suspended: boolean
  suspended_category: string | null
  suspended_detail: string | null
  suspended_at: number | null
}

export type RuntimeToolMetadata = {
  name?: string
  description?: string
  requires_approval?: boolean
  [key: string]: unknown
}

type RuntimeEventBase = {
  type: string
  session_id?: string
  turn_id?: string
  request_id?: string
  schema_version?: string
  timestamp?: number
}

export type ReadyEvent = {
  type: 'ready'
  session_id: string
  schema_version: string
  path: string
  tools: RuntimeToolMetadata[]
  session_state: RuntimeSessionState
}

export type TurnStartedEvent = RuntimeEventBase & {
  type: 'turn_started'
  session_id: string
  turn_id: string
  session_state: RuntimeSessionState
}

export type AssistantTokenEvent = RuntimeEventBase & {
  type: 'assistant_token'
  token: string
}

export type ToolCallStartedEvent = RuntimeEventBase & {
  type: 'tool_call_started'
  name: string
  arguments: string
}

export type ToolCallResultEvent = RuntimeEventBase & {
  type: 'tool_call_result'
  name: string
  ok: boolean
  content: string
  metadata: Record<string, unknown>
}

export type PermissionRequestEvent = RuntimeEventBase & {
  type: 'permission_request'
  tool: string
  arguments: string
  detail: string
  metadata: Record<string, unknown>
}

export type PermissionDecisionAckEvent = RuntimeEventBase & {
  type: 'permission_decision_ack'
  approved: boolean
}

export type SessionStateEvent = RuntimeEventBase & {
  type: 'session_suspended' | 'session_blocked' | 'session_resumed'
  detail?: string
  category?: string
  previous_state?: RuntimeSessionState
  session_state: RuntimeSessionState
}

export type FinalAnswerEvent = RuntimeEventBase & {
  type: 'final_answer'
  content: string
  session_state: RuntimeSessionState
}

export type RuntimeErrorEvent = RuntimeEventBase & {
  type: 'error'
  message: string
  received_type?: string
  received_request_id?: string
}

export type RuntimeEvent =
  | ReadyEvent
  | TurnStartedEvent
  | AssistantTokenEvent
  | ToolCallStartedEvent
  | ToolCallResultEvent
  | PermissionRequestEvent
  | PermissionDecisionAckEvent
  | SessionStateEvent
  | FinalAnswerEvent
  | RuntimeErrorEvent

export type RuntimeClientPacket =
  | {
      type: 'user_input'
      content: string
      turn_id?: string
    }
  | {
      type: 'permission_decision'
      request_id?: string
      approved: boolean
    }
  | {
      type: 'resume_session'
      turn_id?: string
    }
