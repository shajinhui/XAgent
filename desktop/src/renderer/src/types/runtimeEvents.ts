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

export type RuntimeSkillPolicy = {
  allow_implicit_invocation?: boolean
}

export type RuntimeSkillInstall = {
  source_type: string
  source: string
  installed_at: string
}

export type RuntimeSkillDependencyStatus = {
  tools: Array<{
    name: string
    available: boolean
  }>
  missing_tools: string[]
}

export type RuntimeInstallableSkill = {
  id: string
  name: string
  description: string
  source_type: 'github' | string
  source: string
  tags: string[]
  icon?: string
  dependencies?: Record<string, unknown>
  dependency_status?: RuntimeSkillDependencyStatus
  version?: string
  installed?: boolean
  installed_path?: string
  installed_version?: string
  update_available?: boolean
  ref?: string
  path?: string
}

export type RuntimeSkillMetadata = {
  name: string
  description: string
  path: string
  scope: string
  source: string
  enabled?: boolean
  short_description?: string
  icon?: string
  version?: string
  policy?: RuntimeSkillPolicy
  dependencies?: Record<string, unknown>
  dependency_status?: RuntimeSkillDependencyStatus
  install?: RuntimeSkillInstall
}

export type RuntimeSkillLoadError = {
  path: string
  message: string
}

export type RuntimeSkillRegistryError = {
  source: string
  message: string
}

export type RuntimeSkillResource = {
  resource: string
  path: string
  size: number
}

export type RuntimeSkillSelection = {
  name: string
  path: string
}

export type RuntimeSkillDraft = {
  path?: string
  scope: 'repo' | 'user'
  package_template?: 'basic' | 'standard'
  name: string
  description: string
  short_description?: string
  icon?: string
  allow_implicit_invocation: boolean
  content: string
}

export type RuntimeSkillImportDraft = {
  scope: 'repo' | 'user'
  source_path: string
}

export type RuntimeSkillInstallDraft = {
  scope: 'repo' | 'user'
  source_type: 'github'
  source: string
}

export type RuntimeSkillResourceDraft = {
  skill_path: string
  resource: string
  content: string
}

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
  workspace?: RuntimeWorkspace
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
  | 'skill'
  | 'tool'
  | 'error'

export type RuntimeActivityStep = {
  label: string
  status: RuntimeActivityStepStatus
  kind: RuntimeActivityStepKind
  detail?: string
  requestId?: string
  toolName?: string
}

export type RuntimeTaskListItem = {
  step: string
  status: 'pending' | 'in_progress' | 'completed' | 'error'
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

export type TaskListEvent = RuntimeEventBase & {
  type: 'task_list'
  items: RuntimeTaskListItem[]
  model: string
  source?: 'auto' | 'plan_confirmed' | string
  plan_id?: string | null
}

export type PlanPendingEvent = RuntimeEventBase & {
  type: 'plan_pending'
  plan_id: string
  content: string
  summary?: string
  plan_markdown?: string
  items: RuntimeTaskListItem[]
  model: string
  created_at: number
  session_state: RuntimeSessionState
}

export type PlanCancelledEvent = RuntimeEventBase & {
  type: 'plan_cancelled'
  plan_id: string
  session_state: RuntimeSessionState
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

export type RuntimePatchChange = {
  path: string
  change_type: 'add' | 'update' | 'delete' | string
  unified_diff: string
  additions: number
  deletions: number
  move_path?: string | null
}

export type RuntimePatchTestResult = {
  patch_id?: string
  command?: string
  timeout?: number
  source?: string
  changed_paths?: string[]
  started_at?: number
  ok?: boolean
  exit_code?: number | null
  stdout?: string
  stderr?: string
  output?: string
  duration_seconds?: number
  error?: string
  blocked?: boolean
  category?: string
}

export type RuntimePatchMetadata = Record<string, unknown> & {
  partial_apply?: boolean
  applied_paths?: string[]
  remaining_paths?: string[]
  written_paths?: string[]
  failed_path?: string
  partially_written?: boolean
  rolled_back_paths?: string[]
  rollback_failed_path?: string
  rollback_remaining_paths?: string[]
  rollback_partially_written?: boolean
  failure_stage?: string
  rollback_failure_stage?: string
  test_result?: RuntimePatchTestResult
  test_results?: RuntimePatchTestResult[]
  test_command?: string
  test_ok?: boolean
  test_exit_code?: number | null
  test_output?: string
  test_source?: string
}

export type PatchLifecycleEvent = RuntimeEventBase & {
  type:
    | 'patch_proposed'
    | 'patch_approval_request'
    | 'patch_applied'
    | 'patch_rejected'
    | 'patch_apply_failed'
    | 'patch_rolled_back'
  tool: string
  patch_id: string
  patch_status?: string
  summary?: string | null
  changed_paths: string[]
  additions?: number | null
  deletions?: number | null
  changes: RuntimePatchChange[]
  metadata: RuntimePatchMetadata
}

export type SkillsListedEvent = RuntimeEventBase & {
  type: 'skills_listed'
  skills: RuntimeSkillMetadata[]
  errors: RuntimeSkillLoadError[]
  workspace: RuntimeWorkspace
}

export type InstallableSkillsListedEvent = RuntimeEventBase & {
  type: 'installable_skills_listed'
  installable_skills: RuntimeInstallableSkill[]
  errors: RuntimeSkillRegistryError[]
  workspace: RuntimeWorkspace
}

export type SkillUsedEvent = RuntimeEventBase & {
  type: 'skill_used'
  name: string
  path: string
  scope: string
  invocation_type: 'explicit' | 'implicit' | string
}

export type SkillWarningEvent = RuntimeEventBase & {
  type: 'skill_warning'
  message: string
}

export type SkillLoadedEvent = RuntimeEventBase & {
  type: 'skill_loaded'
  skill: RuntimeSkillMetadata
  content: string
  workspace: RuntimeWorkspace
}

export type SkillSavedEvent = RuntimeEventBase & {
  type: 'skill_saved'
  action: 'create' | 'update' | 'import' | string
  skill: RuntimeSkillMetadata
  skills: RuntimeSkillMetadata[]
  errors: RuntimeSkillLoadError[]
  workspace: RuntimeWorkspace
}

export type SkillDeletedEvent = RuntimeEventBase & {
  type: 'skill_deleted'
  action: 'delete' | string
  skill: RuntimeSkillMetadata
  skills: RuntimeSkillMetadata[]
  errors: RuntimeSkillLoadError[]
  workspace: RuntimeWorkspace
}

export type SkillErrorEvent = RuntimeEventBase & {
  type: 'skill_error'
  message: string
  workspace?: RuntimeWorkspace
}

export type SkillResourcesListedEvent = RuntimeEventBase & {
  type: 'skill_resources_listed'
  skill: RuntimeSkillMetadata
  resources: RuntimeSkillResource[]
  workspace: RuntimeWorkspace
}

export type SkillResourceLoadedEvent = RuntimeEventBase & {
  type: 'skill_resource_loaded'
  skill: RuntimeSkillMetadata
  resource: RuntimeSkillResource
  content: string
  workspace: RuntimeWorkspace
}

export type SkillResourceSavedEvent = RuntimeEventBase & {
  type: 'skill_resource_saved'
  skill: RuntimeSkillMetadata
  resource: RuntimeSkillResource
  resources: RuntimeSkillResource[]
  workspace: RuntimeWorkspace
}

export type SkillResourceDeletedEvent = RuntimeEventBase & {
  type: 'skill_resource_deleted'
  skill: RuntimeSkillMetadata
  resource: RuntimeSkillResource
  resources: RuntimeSkillResource[]
  workspace: RuntimeWorkspace
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
  pending_patch_review?: PatchLifecycleEvent | null
  workspace?: RuntimeWorkspace
}

export type FinalAnswerEvent = RuntimeEventBase & {
  type: 'final_answer'
  content: string
  session_state: RuntimeSessionState
  changed_files?: Array<{ path: string; can_undo: boolean; additions?: number; deletions?: number }>
}

export type FileUndoneEvent = RuntimeEventBase & {
  type: 'file_undone'
  file_path: string
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
  | TaskListEvent
  | PlanPendingEvent
  | PlanCancelledEvent
  | SessionCreatedEvent
  | WorkspaceChangedEvent
  | WorkspacePolicyChangedEvent
  | AssistantTokenEvent
  | ToolCallStartedEvent
  | ToolCallResultEvent
  | PatchLifecycleEvent
  | SkillsListedEvent
  | InstallableSkillsListedEvent
  | SkillUsedEvent
  | SkillWarningEvent
  | SkillLoadedEvent
  | SkillSavedEvent
  | SkillDeletedEvent
  | SkillErrorEvent
  | SkillResourcesListedEvent
  | SkillResourceLoadedEvent
  | SkillResourceSavedEvent
  | SkillResourceDeletedEvent
  | PermissionRequestEvent
  | PermissionDecisionAckEvent
  | ClarificationRequestEvent
  | ClarificationResponseAckEvent
  | SessionStateEvent
  | FinalAnswerEvent
  | FileUndoneEvent
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
      selected_skills?: RuntimeSkillSelection[]
      turn_id?: string
    }
  | {
      type: 'plan_request'
      content: string
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'plan_confirm'
      plan_id?: string
      request_id?: string
      turn_id?: string
      model?: string
      reasoning_effort?: RuntimeReasoningEffort
      selected_skills?: RuntimeSkillSelection[]
    }
  | {
      type: 'plan_cancel'
      plan_id?: string
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'apply_patch_review'
      patch_id: string
      selected_paths?: string[]
      test_command?: string
      test_timeout?: number
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'reject_patch_review'
      patch_id: string
      reason?: string
      request_id?: string
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
      request_id?: string
      turn_id?: string
    }
  | {
      type: 'list_sessions'
      request_id?: string
      limit?: number
      turn_id?: string
    }
  | {
      type: 'list_skills'
      request_id?: string
      force_reload?: boolean
      turn_id?: string
    }
  | {
      type: 'list_installable_skills'
      request_id?: string
      force_reload?: boolean
      turn_id?: string
    }
  | {
      type: 'get_skill'
      request_id?: string
      path: string
      turn_id?: string
    }
  | {
      type: 'create_skill'
      request_id?: string
      scope: 'repo' | 'user'
      name: string
      description: string
      short_description?: string
      icon?: string
      allow_implicit_invocation?: boolean
      content?: string
      package_template?: 'basic' | 'standard'
      turn_id?: string
    }
  | {
      type: 'import_skill'
      request_id?: string
      scope: 'repo' | 'user'
      source_path: string
      turn_id?: string
    }
  | {
      type: 'install_skill'
      request_id?: string
      scope: 'repo' | 'user'
      source_type: 'github'
      source: string
      turn_id?: string
    }
  | {
      type: 'install_registry_skill'
      request_id?: string
      scope: 'repo' | 'user'
      id: string
      turn_id?: string
    }
  | {
      type: 'reinstall_skill'
      request_id?: string
      path: string
      turn_id?: string
    }
  | {
      type: 'update_skill'
      request_id?: string
      path: string
      name: string
      description: string
      short_description?: string
      icon?: string
      allow_implicit_invocation?: boolean
      content?: string
      turn_id?: string
    }
  | {
      type: 'delete_skill'
      request_id?: string
      path: string
      turn_id?: string
    }
  | {
      type: 'list_skill_resources'
      request_id?: string
      path: string
      turn_id?: string
    }
  | {
      type: 'get_skill_resource'
      request_id?: string
      path: string
      resource: string
      turn_id?: string
    }
  | {
      type: 'save_skill_resource'
      request_id?: string
      path: string
      resource: string
      content: string
      turn_id?: string
    }
  | {
      type: 'delete_skill_resource'
      request_id?: string
      path: string
      resource: string
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
      type: 'undo_file'
      file_path: string
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
