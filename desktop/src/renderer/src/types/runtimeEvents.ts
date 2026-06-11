export type RuntimeSessionState = {
  status: 'active' | 'suspended'
  turn_in_progress: boolean
  active_turn_id: string | null
  turn_started_at: number | null
  cancellation_requested: boolean
  suspended: boolean
  suspended_category: string | null
  suspended_detail: string | null
  suspended_at: number | null
}

export type RuntimeReasoningEffort = 'off' | 'low' | 'medium' | 'high' | 'max'
export type RuntimePermissionMode = 'request_approval' | 'auto_approve' | 'full_access' | 'custom'

export type RuntimeModelConfig = {
  default_model: string
  model_options: string[]
  reasoning_effort: RuntimeReasoningEffort
  reasoning_effort_options: RuntimeReasoningEffort[]
}

export type RuntimeToolMetadata = {
  name?: string
  is_read_only?: boolean
  is_mutating?: boolean
  supports_parallel?: boolean
  description?: string
  requires_approval?: boolean
  [key: string]: unknown
}

export type RuntimeToolMetadataMap = Record<string, RuntimeToolMetadata>

export type RuntimeWorkspaceTrust = {
  level: 'trusted' | 'untrusted' | 'session_only'
  trust_key: string
  source: 'default' | 'user_config' | 'session'
  project_config_enabled: boolean
}

export type RuntimeAdditionalRoot = {
  path: string
  access: 'read' | 'write'
  source: 'user' | 'session' | 'config'
}

export type RuntimeWorkspacePolicy = {
  source: 'defaults' | 'project_config' | 'runtime_mode'
  config_path: string | null
  permission_mode: RuntimePermissionMode
  permission_profile: 'read_only' | 'workspace_write' | 'danger_no_sandbox'
  approval_policy: 'ask-before-mutating' | 'auto' | 'never'
  network_policy: 'restricted' | 'enabled'
  exec_rule_count: number
}

export type RuntimeWorkspace = {
  selected_root: string
  project_root: string
  current_dir: string
  display_name: string
  git_root: string | null
  trust: RuntimeWorkspaceTrust
  additional_roots: RuntimeAdditionalRoot[]
  policy?: RuntimeWorkspacePolicy
}

export type RuntimeWorkspaceProject = RuntimeWorkspace & {
  updated_at: number
}

export type ConversationTitleMessage = {
  role: 'user' | 'assistant'
  content: string
}

export type RuntimeSessionSummary = {
  session_id: string
  title: string
  created_at: number
  updated_at: number
  last_turn_id: string | null
  message_count: number
  last_message: string
}

export type RuntimeActivityStepStatus = 'running' | 'waiting' | 'success' | 'error'

export type RuntimeActivityStepKind =
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

export type RuntimeActivityStep = {
  label: string
  status: RuntimeActivityStepStatus
  kind: RuntimeActivityStepKind
  detail?: string
  requestId?: string
}

export type RuntimeDisplayMessage = {
  role: 'user' | 'assistant' | 'activity' | 'activity_event'
  content: string
  timestamp?: number
  collapsed?: boolean
  startedAt?: number
  finishedAt?: number
  activity_key?: string
  step?: RuntimeActivityStep
  isFinal?: boolean
}

export type ClarificationOption = {
  id?: string
  label: string
  description?: string
  recommended?: boolean
}

export type ClarificationResponsePayload = {
  choice_id?: string
  option_index?: number
  content?: string
  skipped?: boolean
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
  tools: RuntimeToolMetadataMap
  session_state: RuntimeSessionState
  workspace?: RuntimeWorkspace
  model_config?: RuntimeModelConfig
}

export type TurnStartedEvent = RuntimeEventBase & {
  type: 'turn_started'
  session_id: string
  turn_id: string
  session_state: RuntimeSessionState
  model_config?: {
    model: string
    reasoning_effort: RuntimeReasoningEffort
  }
}

export type SessionCreatedEvent = RuntimeEventBase & {
  type: 'session_created'
  session_id: string
  previous_state: RuntimeSessionState
  session_state: RuntimeSessionState
  workspace?: RuntimeWorkspace
}

export type WorkspaceChangedEvent = RuntimeEventBase & {
  type: 'workspace_changed'
  session_id: string
  previous_workspace: RuntimeWorkspace
  workspace: RuntimeWorkspace
  previous_state: RuntimeSessionState
  session_state: RuntimeSessionState
  tools: RuntimeToolMetadataMap
}

export type WorkspacePolicyChangedEvent = RuntimeEventBase & {
  type: 'workspace_policy_changed'
  session_id: string
  previous_workspace: RuntimeWorkspace
  workspace: RuntimeWorkspace
  session_state: RuntimeSessionState
  reason:
    | 'change_directory'
    | 'add_dir'
    | 'trust_workspace'
    | 'untrust_workspace'
    | 'set_permission_mode'
    | string
  current_dir?: string
  added_root?: RuntimeAdditionalRoot
  permission_mode?: RuntimePermissionMode
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

export type ClarificationRequestEvent = RuntimeEventBase & {
  type: 'clarification_request'
  tool: string
  question: string
  options: ClarificationOption[]
  allow_freeform: boolean
  metadata: Record<string, unknown>
}

export type ClarificationResponseAckEvent = RuntimeEventBase & {
  type: 'clarification_response_ack'
  skipped: boolean
}

export type SessionStateEvent = RuntimeEventBase & {
  type:
    | 'session_suspended'
    | 'session_blocked'
    | 'session_resumed'
    | 'session_busy'
    | 'turn_cancelling'
    | 'turn_cancelled'
  detail?: string
  category?: string
  previous_state?: RuntimeSessionState
  session_state: RuntimeSessionState
  resumed_from_disk?: boolean
  message_count?: number
  messages?: RuntimeDisplayMessage[]
  session?: RuntimeSessionSummary | null
  workspace?: RuntimeWorkspace
}

export type FinalAnswerEvent = RuntimeEventBase & {
  type: 'final_answer'
  content: string
  session_state: RuntimeSessionState
}

export type RuntimeErrorEvent = RuntimeEventBase & {
  type: 'error' | 'workspace_error'
  message: string
  received_type?: string
  received_request_id?: string
  requested_workspace?: string
  requested_path?: string
  requested_permission_mode?: RuntimePermissionMode | string
  workspace?: RuntimeWorkspace
}

export type ConversationTitleEvent = RuntimeEventBase & {
  type: 'conversation_title'
  title: string
  model: string
}

export type SessionsListEvent = RuntimeEventBase & {
  type: 'sessions_list'
  sessions: RuntimeSessionSummary[]
  workspace?: RuntimeWorkspace
}

export type SessionDeletedEvent = RuntimeEventBase & {
  type: 'session_deleted'
  deleted_session_id: string
  deleted_current: boolean
  session_state: RuntimeSessionState
  workspace?: RuntimeWorkspace
  sessions: RuntimeSessionSummary[]
}

export type RuntimeEvent =
  | ReadyEvent
  | TurnStartedEvent
  | SessionCreatedEvent
  | WorkspaceChangedEvent
  | WorkspacePolicyChangedEvent
  | AssistantTokenEvent
  | ToolCallStartedEvent
  | ToolCallResultEvent
  | PermissionRequestEvent
  | PermissionDecisionAckEvent
  | ClarificationRequestEvent
  | ClarificationResponseAckEvent
  | SessionStateEvent
  | FinalAnswerEvent
  | ConversationTitleEvent
  | SessionsListEvent
  | SessionDeletedEvent
  | RuntimeErrorEvent

export type RuntimeClientPacket =
  | {
      type: 'user_input'
      content: string
      model?: string
      reasoning_effort?: RuntimeReasoningEffort
      turn_id?: string
    }
  | {
      type: 'permission_decision'
      request_id?: string
      approved: boolean
      feedback?: string
      scope?: 'once' | 'session'
      prefix_rule?: string[]
    }
  | ({
      type: 'clarification_response'
      request_id?: string
    } & ClarificationResponsePayload)
  | {
      type: 'resume_session'
      session_id?: string
      turn_id?: string
    }
  | {
      type: 'list_sessions'
      request_id?: string
      limit?: number
      turn_id?: string
    }
  | {
      type: 'delete_session'
      session_id: string
      workspace_path?: string
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'new_session'
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'cancel_turn'
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'open_workspace'
      path: string
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'change_directory'
      path: string
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'add_dir'
      path: string
      access: 'read' | 'write'
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'trust_workspace' | 'untrust_workspace'
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'set_permission_mode'
      mode: RuntimePermissionMode
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'conversation_title_request'
      request_id?: string
      messages: ConversationTitleMessage[]
    }
