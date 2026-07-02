import { defineStore } from 'pinia'
import { RuntimeSocket, type RuntimeConnectionStatus } from '@renderer/services/runtimeSocket'
import { useChatStore } from '@renderer/stores/chat'
import type { ActivityStepKind, ActivityStepStatus } from '@renderer/stores/chat'
import type {
  ClarificationRequestEvent,
  ClarificationResponsePayload,
  PatchLifecycleEvent,
  PermissionRequestEvent,
  PlanPendingEvent,
  RuntimeEvent,
  RuntimeInstallableSkill,
  RuntimeModelConfig,
  RuntimePermissionMode,
  RuntimeReasoningEffort,
  RuntimeSkillDraft,
  RuntimeSkillImportDraft,
  RuntimeSkillInstallDraft,
  RuntimeSkillLoadError,
  RuntimeSkillMetadata,
  RuntimeSkillResource,
  RuntimeSkillResourceDraft,
  RuntimeSkillRegistryError,
  RuntimeSkillSelection,
  RuntimeSessionState,
  RuntimeSessionSummary,
  RuntimeTaskListItem,
  RuntimeAdditionalRoot,
  RuntimePatchMetadata,
  RuntimePatchTestResult,
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
const PERMISSION_MODE_STORAGE_KEY = 'codex-mini.permission-mode'
const PLAN_MODE_STORAGE_KEY = 'codex-mini.plan-mode'
const MAX_WORKSPACE_PROJECTS = 20
const MAX_CONVERSATION_WORKSPACES = 12
const FALLBACK_MODEL_OPTIONS = ['gpt-4o-mini']
const FALLBACK_REASONING_OPTIONS: RuntimeReasoningEffort[] = ['off', 'low', 'medium', 'high', 'max']
const DEFAULT_PERMISSION_MODE: RuntimePermissionMode = 'request_approval'
const PERMISSION_MODES: RuntimePermissionMode[] = [
  'request_approval',
  'auto_approve',
  'full_access',
  'custom'
]

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

function isPlanningTool(name: string): boolean {
  return name === 'update_plan' || name === 'create_task_list'
}

function normalizeTaskStep(value: unknown): string {
  return String(value || '')
    .replace(/^[-*•○⋯✓✔✗\d.)、\s]+/, '')
    .trim()
}

function normalizeTaskListStatus(value: unknown, index: number): RuntimeTaskListItem['status'] {
  const status = String(value || '')
    .trim()
    .toLowerCase()
  if (['completed', 'complete', 'done', 'success', '✓', '✔', 'x'].includes(status)) {
    return 'completed'
  }
  if (['in_progress', 'running', 'current', 'progress', '⋯', '…'].includes(status)) {
    return 'in_progress'
  }
  if (['error', 'failed', 'failure', '✗'].includes(status)) return 'error'
  if (status === 'pending' || status === 'todo' || status === '○') return 'pending'
  return index === 0 ? 'in_progress' : 'pending'
}

function normalizeTaskListFromValue(value: unknown): RuntimeTaskListItem[] {
  const rawItems =
    typeof value === 'object' && value !== null && !Array.isArray(value)
      ? ((value as Record<string, unknown>).plan ??
        (value as Record<string, unknown>).tasks ??
        (value as Record<string, unknown>).items)
      : value

  if (!Array.isArray(rawItems)) return []

  return rawItems
    .map((item, index): RuntimeTaskListItem | null => {
      if (typeof item === 'object' && item !== null) {
        const candidate = item as Record<string, unknown>
        const step = normalizeTaskStep(candidate.step ?? candidate.task ?? candidate.label)
        if (!step) return null
        return {
          step,
          status: normalizeTaskListStatus(candidate.status, index)
        }
      }

      const step = normalizeTaskStep(item)
      if (!step) return null
      return {
        step,
        status: normalizeTaskListStatus(undefined, index)
      }
    })
    .filter((item): item is RuntimeTaskListItem => Boolean(item))
}

function extractTaskListFromToolArguments(
  name: string,
  rawArguments: string
): RuntimeTaskListItem[] {
  if (name !== 'update_plan') return []
  return normalizeTaskListFromValue(parseToolArguments(rawArguments).plan)
}

function extractTaskListFromToolResult(name: string, content: string): RuntimeTaskListItem[] {
  if (!isPlanningTool(name)) return []

  try {
    const parsed = JSON.parse(content)
    const tasks = normalizeTaskListFromValue(parsed)
    if (tasks.length) return tasks
  } catch {
    // 规划工具通常返回人类可读文本，这里继续走文本解析。
  }

  const tasks: RuntimeTaskListItem[] = []
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim()
    const match = trimmed.match(/^([○⋯…✓✔✗-])\s*\d+[.)、]?\s*(.+)$/)
    if (!match) continue

    const step = normalizeTaskStep(match[2])
    if (!step) continue
    tasks.push({
      step,
      status: normalizeTaskListStatus(match[1], tasks.length)
    })
  }

  return tasks
}

function getToolKind(name: string): ActivityStepKind {
  if (name === 'grep') return 'search'
  if (name === 'read_file') return 'read'
  if (name === 'write_file' || name === 'edit_file') return 'edit'
  if (name === 'apply_patch' || name === 'reject_patch' || name === 'rollback_patch') return 'edit'
  if (name === 'run_command') return 'command'
  if (name === 'web_fetch') return 'web'
  if (name === 'read_skill' || name === 'read_skill_resource') return 'skill'
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
    case 'apply_patch':
      return '正在准备应用 Patch'
    case 'reject_patch':
      return '正在准备拒绝 Patch'
    case 'rollback_patch':
      return '正在准备回滚 Patch'
    case 'run_command':
      return '正在准备运行命令'
    case 'web_fetch':
      return '正在获取网页'
    case 'read_skill':
      return '正在读取 Skill'
    case 'read_skill_resource':
      return '正在读取 Skill 资源'
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
      case 'apply_patch':
        return 'Patch 应用失败'
      case 'reject_patch':
        return 'Patch 拒绝失败'
      case 'rollback_patch':
        return 'Patch 回滚失败'
      case 'run_command':
        return '命令失败'
      case 'web_fetch':
        return '获取失败'
      case 'read_skill':
      case 'read_skill_resource':
        return 'Skill 读取失败'
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
    case 'apply_patch':
      return 'Patch 已应用'
    case 'reject_patch':
      return 'Patch 已拒绝'
    case 'rollback_patch':
      return 'Patch 已回滚'
    case 'run_command':
      return '已运行 1 条命令'
    case 'web_fetch':
      return '已获取 1 个网页'
    case 'read_skill':
      return '已读取 1 个 Skill'
    case 'read_skill_resource':
      return '已读取 1 个 Skill 资源'
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

function shortPatchId(patchId: string | undefined): string {
  const clean = String(patchId || '').trim()
  return clean ? clean.slice(0, 8) : 'patch'
}

function asObjectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function latestPatchTestResult(event: PatchLifecycleEvent): RuntimePatchTestResult | null {
  const metadata = (event.metadata || {}) as RuntimePatchMetadata
  const direct = asObjectRecord(metadata.test_result)
  if (direct) return direct as RuntimePatchTestResult

  const history = metadata.test_results
  if (!Array.isArray(history) || history.length === 0) return null

  for (let index = history.length - 1; index >= 0; index -= 1) {
    const item = asObjectRecord(history[index])
    if (item) return item as RuntimePatchTestResult
  }
  return null
}

function formatPatchTestSource(source: string | undefined): string | null {
  if (source === 'explicit') return '显式测试命令'
  if (source === 'project_config') return '项目默认测试命令'
  if (source === 'agents_md') return 'AGENTS.md 默认测试命令'
  const clean = String(source || '').trim()
  return clean || null
}

function formatPatchTestDuration(value: number | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  if (value < 10) return `${value.toFixed(1)}s`
  return `${Math.round(value)}s`
}

function patchTestHeadline(result: RuntimePatchTestResult): string {
  const status = result.blocked ? '测试被安全策略拦截' : result.ok ? '测试通过' : '测试失败'
  const extras: string[] = []
  if (typeof result.exit_code === 'number') extras.push(`exit ${result.exit_code}`)
  const source = formatPatchTestSource(result.source)
  if (source) extras.push(source)
  const duration = formatPatchTestDuration(result.duration_seconds)
  if (duration) extras.push(duration)
  return extras.length ? `${status} · ${extras.join(' · ')}` : status
}

function patchTestOutput(result: RuntimePatchTestResult): string | null {
  const output = String(result.output || '').trim()
  if (!output) return null
  return result.ok && !result.blocked ? null : output
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter((item) => item.length > 0)
}

function formatPatchFailureStage(stage: string | undefined): string | null {
  if (stage === 'validate') return '校验'
  if (stage === 'rollback_validate') return '回滚校验'
  if (stage === 'preflight') return 'Git 预检'
  if (stage === 'write') return '写入'
  const clean = String(stage || '').trim()
  return clean || null
}

function formatPatchFailureDetail(metadata: RuntimePatchMetadata): string | null {
  const lines: string[] = []
  const stage = formatPatchFailureStage(metadata.failure_stage)
  if (stage) lines.push(`失败阶段: ${stage}`)

  const writtenPaths = stringArray(metadata.written_paths)
  if (writtenPaths.length) {
    lines.push('已写入:')
    lines.push(writtenPaths.map((path) => '- ' + path).join('\n'))
  }

  const failedPath = String(metadata.failed_path || '').trim()
  if (failedPath) {
    lines.push('失败文件:')
    lines.push(`- ${failedPath}`)
    if (metadata.partially_written === true) {
      lines.push('提示: 失败文件可能已经部分写入。')
    }
  }

  const remainingPaths = stringArray(metadata.remaining_paths)
  if (remainingPaths.length) {
    lines.push('未触及:')
    lines.push(remainingPaths.map((path) => '- ' + path).join('\n'))
  }

  return lines.length ? lines.join('\n') : null
}

function formatPatchTestDetail(result: RuntimePatchTestResult): string {
  const lines = ['测试:', `- ${patchTestHeadline(result)}`]
  const command = String(result.command || '').trim()
  if (command) lines.push(`- 命令: ${command}`)
  const output = patchTestOutput(result)
  if (output) {
    lines.push('- 输出:')
    lines.push(output)
  }
  return lines.join('\n')
}

function formatPatchLifecycleLabel(event: PatchLifecycleEvent): string {
  const patchId = shortPatchId(event.patch_id)
  switch (event.type) {
    case 'patch_proposed':
      if (event.metadata?.rolled_back === true) return 'Patch 已回滚并恢复待审查：' + patchId
      if (event.metadata?.partial_apply === true) return 'Patch 已更新：' + patchId
      return 'Patch 已生成：' + patchId
    case 'patch_approval_request':
      return '等待 Patch 审批：' + patchId
    case 'patch_applied':
      return 'Patch 已应用：' + patchId
    case 'patch_rejected':
      return 'Patch 已拒绝：' + patchId
    case 'patch_apply_failed':
      return 'Patch 应用失败：' + patchId
    case 'patch_rolled_back':
      return 'Patch 已回滚：' + patchId
    default:
      return 'Patch 状态更新：' + patchId
  }
}

function patchLifecycleStatus(event: PatchLifecycleEvent): ActivityStepStatus {
  if (event.type === 'patch_approval_request') return 'waiting'
  if (event.type === 'patch_apply_failed') return 'error'
  return 'success'
}

function formatPatchLifecycleDetail(event: PatchLifecycleEvent): string | undefined {
  const details: string[] = []
  if (event.summary) details.push(event.summary)
  const statParts: string[] = []
  if (typeof event.additions === 'number') statParts.push('+' + event.additions)
  if (typeof event.deletions === 'number') statParts.push('-' + event.deletions)
  if (statParts.length) details.push(statParts.join(' / '))
  if (event.changed_paths?.length) {
    details.push(event.changed_paths.slice(0, 8).map((path) => '- ' + path).join('\n'))
  }
  const failureDetail = formatPatchFailureDetail((event.metadata || {}) as RuntimePatchMetadata)
  if (failureDetail) {
    details.push(failureDetail)
  }
  const testResult = latestPatchTestResult(event)
  if (testResult) {
    details.push(formatPatchTestDetail(testResult))
  }
  return details.length ? details.join('\n') : undefined
}

function formatPatchSystemMessage(event: PatchLifecycleEvent): string | null {
  if (event.type === 'patch_approval_request') return null
  const lines = [formatPatchLifecycleLabel(event)]
  const failureDetail = formatPatchFailureDetail((event.metadata || {}) as RuntimePatchMetadata)
  if (failureDetail) {
    lines.push(failureDetail)
  }
  const testResult = latestPatchTestResult(event)
  if (testResult) {
    lines.push(patchTestHeadline(testResult))
    const command = String(testResult.command || '').trim()
    if (command) lines.push(command)
    const output = patchTestOutput(testResult)
    if (output) lines.push(output)
  }
  return lines.join('\n')
}

function normalizePatchSelection(paths: string[]): string[] {
  return Array.from(
    new Set(paths.map((path) => String(path || '').trim()).filter((path) => path.length > 0))
  )
}

function formatPermissionModeLabel(mode: RuntimePermissionMode | string | undefined): string {
  if (mode === 'auto_approve') return '替我审批'
  if (mode === 'full_access') return '完全访问'
  if (mode === 'custom') return '自定义'
  return '请求批准'
}

function normalizePermissionMode(value: unknown): RuntimePermissionMode | null {
  return typeof value === 'string' && PERMISSION_MODES.includes(value as RuntimePermissionMode)
    ? (value as RuntimePermissionMode)
    : null
}

function loadGlobalPermissionMode(): RuntimePermissionMode {
  return (
    normalizePermissionMode(window.localStorage.getItem(PERMISSION_MODE_STORAGE_KEY)) ||
    DEFAULT_PERMISSION_MODE
  )
}

function saveGlobalPermissionMode(mode: RuntimePermissionMode): void {
  window.localStorage.setItem(PERMISSION_MODE_STORAGE_KEY, mode)
}

function loadPlanModeEnabled(): boolean {
  return window.localStorage.getItem(PLAN_MODE_STORAGE_KEY) === 'true'
}

function savePlanModeEnabled(enabled: boolean): void {
  window.localStorage.setItem(PLAN_MODE_STORAGE_KEY, enabled ? 'true' : 'false')
}

function clearReconnectTimer(): void {
  if (!reconnectTimer) return

  window.clearTimeout(reconnectTimer)
  reconnectTimer = null
}

function normalizeSelectedRoot(root: string): string {
  return root.replace(/[\\/]+$/, '')
}

function normalizeSkillPath(path: string): string {
  return path.trim().replace(/[\\/]+$/, '')
}

function formatWorkspacePath(workspace: RuntimeWorkspace, path: string): string {
  const normalizedRoot = normalizeSelectedRoot(workspace.selected_root)
  const normalizedPath = normalizeSelectedRoot(path)
  if (normalizedPath === normalizedRoot) return '.'

  const slashRoot = `${normalizedRoot}/`
  if (normalizedPath.startsWith(slashRoot)) {
    return normalizedPath.slice(slashRoot.length)
  }
  return normalizedPath
}

function isDefaultConversationSelectedRoot(root: string): boolean {
  return /[\\/]Documents[\\/]Codex[\\/]\d{4}-\d{2}-\d{2}[\\/]new-chat$/.test(
    normalizeSelectedRoot(root)
  )
}

function normalizeModelOption(value: unknown): string | null {
  if (typeof value !== 'string') return null

  const model = value.trim()
  if (!model || model.length > 160 || /\s/.test(model)) return null
  return model
}

function uniqueModelOptions(values: unknown[], fallback = FALLBACK_MODEL_OPTIONS): string[] {
  const options = values
    .map((value) => normalizeModelOption(value))
    .filter((value): value is string => Boolean(value))

  if (options.length > 0) {
    return Array.from(new Set(options))
  }

  return Array.from(
    new Set(
      fallback
        .map((value) => normalizeModelOption(value))
        .filter((value): value is string => Boolean(value))
    )
  )
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
  const storedProjectRoots = new Set(
    loadStoredWorkspaceProjectCandidates()
      .filter(isWorkspaceProject)
      .map((project) => normalizeSelectedRoot(project.selected_root))
      .filter((root) => !isDefaultConversationSelectedRoot(root))
  )

  try {
    const rawValue = window.localStorage.getItem(CONVERSATION_WORKSPACES_STORAGE_KEY)
    const parsed = JSON.parse(rawValue || '[]')
    if (Array.isArray(parsed)) {
      parsed.forEach((root) => {
        if (typeof root === 'string' && root.trim()) {
          const normalizedRoot = normalizeSelectedRoot(root.trim())
          if (
            isDefaultConversationSelectedRoot(normalizedRoot) &&
            !storedProjectRoots.has(normalizedRoot)
          ) {
            roots.add(normalizedRoot)
          }
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
          mergeSessionSummaries([], (sessions as unknown[]).filter(isSessionSummary))
            .filter((session) => sessionBelongsToSelectedRoot(session, root, true))
            .slice(0, 50)
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
    (candidate.source === 'user' || candidate.source === 'session' || candidate.source === 'config')
  )
}

function isRuntimeWorkspace(value: unknown): value is RuntimeWorkspace {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeWorkspace>
  return (
    typeof candidate.selected_root === 'string' &&
    typeof candidate.project_root === 'string' &&
    typeof candidate.current_dir === 'string' &&
    typeof candidate.display_name === 'string' &&
    (typeof candidate.git_root === 'string' || candidate.git_root === null) &&
    isWorkspaceTrust(candidate.trust) &&
    Array.isArray(candidate.additional_roots) &&
    candidate.additional_roots.every(isAdditionalRoot)
  )
}

function isWorkspaceProject(value: unknown): value is RuntimeWorkspaceProject {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<RuntimeWorkspaceProject>
  return isRuntimeWorkspace(value) && typeof candidate.updated_at === 'number'
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
    typeof candidate.last_message === 'string' &&
    (candidate.workspace === undefined || isRuntimeWorkspace(candidate.workspace))
  )
}

function sessionBelongsToSelectedRoot(
  session: RuntimeSessionSummary,
  root: string,
  requireWorkspace = false
): boolean {
  const normalizedRoot = normalizeSelectedRoot(root)
  const workspaceRoot = session.workspace?.selected_root
    ? normalizeSelectedRoot(session.workspace.selected_root)
    : ''
  if (!workspaceRoot) return !requireWorkspace
  return workspaceRoot === normalizedRoot
}

function mergeSessionSummaries(
  current: RuntimeSessionSummary[] = [],
  incoming: RuntimeSessionSummary[] = []
): RuntimeSessionSummary[] {
  const bySessionId = new Map<string, RuntimeSessionSummary>()
  const sessions = [...current, ...incoming]
  sessions.forEach((session) => {
    const existing = bySessionId.get(session.session_id)
    if (!existing || session.updated_at >= existing.updated_at) {
      bySessionId.set(session.session_id, session)
    }
  })

  return Array.from(bySessionId.values()).sort((left, right) => right.updated_at - left.updated_at)
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
    pendingPlan: null as PlanPendingEvent | null,
    pendingPatchReview: null as PatchLifecycleEvent | null,
    latestPatchResult: null as PatchLifecycleEvent | null,
    planRequestInFlight: false,
    pendingPlanRequestId: '',
    planModeEnabled: loadPlanModeEnabled(),
    sessionHistory: [] as RuntimeSessionSummary[],
    sessionsLoading: false,
    selectedSessionId: '',
    selectedSessionRoot: '',
    workspace: null as RuntimeWorkspace | null,
    skills: [] as RuntimeSkillMetadata[],
    skillErrors: [] as RuntimeSkillLoadError[],
    skillsLoading: false,
    installableSkills: [] as RuntimeInstallableSkill[],
    installableSkillErrors: [] as RuntimeSkillRegistryError[],
    installableSkillsLoading: false,
    skillEditorLoading: false,
    skillEditorSkill: null as RuntimeSkillMetadata | null,
    skillEditorContent: '',
    skillManagementError: '',
    skillResources: [] as RuntimeSkillResource[],
    skillResourcesLoading: false,
    skillResourceLoading: false,
    skillResourcePath: '',
    skillResourceContent: '',
    skillResourceError: '',
    selectedSkillPaths: [] as string[],
    workspaceProjects: loadWorkspaceProjects(),
    conversationSelectedRoots: loadConversationSelectedRoots(),
    sessionsBySelectedRoot: loadWorkspaceSessionCache(),
    pendingWorkspaceResume: null as { selected_root: string; sessionId: string } | null,
    pendingConversationSelectedRoot: null as string | null,
    pendingWorkspaceRequestId: '',
    pendingWorkspaceRequestRoot: '',
    pendingResumeRequestId: '',
    pendingResumeRequestRoot: '',
    pendingResumeSessionId: '',
    selectedModel: FALLBACK_MODEL_OPTIONS[0],
    modelOptions: [...FALLBACK_MODEL_OPTIONS],
    reasoningEffort: 'off' as RuntimeReasoningEffort,
    reasoningEffortOptions: [...FALLBACK_REASONING_OPTIONS],
    globalPermissionMode: loadGlobalPermissionMode(),
    tools: {} as RuntimeToolMetadataMap,
    events: [] as RuntimeEvent[]
  }),
  getters: {
    isConnected: (state) => state.connectionStatus === 'connected',
    isConnecting: (state) => state.connectionStatus === 'connecting',
    isSuspended: (state) => Boolean(state.sessionState?.suspended),
    permissionMode: (state): RuntimePermissionMode => state.globalPermissionMode,
    selectedSkills: (state): RuntimeSkillMetadata[] => {
      const selectedPaths = new Set(state.selectedSkillPaths.map(normalizeSkillPath))
      return state.skills.filter((skill) => selectedPaths.has(normalizeSkillPath(skill.path)))
    }
  },
  actions: {
    getActiveWorkspaceRoot(): string {
      return this.workspace?.selected_root
        ? normalizeSelectedRoot(this.workspace.selected_root)
        : ''
    },

    setSelectedSession(sessionId?: string | null, root?: string | null): void {
      this.selectedSessionId = sessionId || ''
      this.selectedSessionRoot = root ? normalizeSelectedRoot(root) : this.getActiveWorkspaceRoot()
    },

    invalidatePendingResume(root?: string | null): void {
      this.pendingResumeRequestId = `ignore-resume-${Date.now()}`
      this.pendingResumeRequestRoot = root
        ? normalizeSelectedRoot(root)
        : this.getActiveWorkspaceRoot()
      this.pendingResumeSessionId = ''
    },

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

    setPlanModeEnabled(enabled: boolean): void {
      this.planModeEnabled = enabled
      savePlanModeEnabled(enabled)
    },

    getSelectedSkillPayload(): RuntimeSkillSelection[] {
      const selectedPaths = new Set(this.selectedSkillPaths.map(normalizeSkillPath))
      return this.skills
        .filter((skill) => selectedPaths.has(normalizeSkillPath(skill.path)))
        .map((skill) => ({
          name: skill.name,
          path: skill.path
        }))
    },

    pruneSelectedSkills(): void {
      const availablePaths = new Set(this.skills.map((skill) => normalizeSkillPath(skill.path)))
      this.selectedSkillPaths = this.selectedSkillPaths.filter((path) =>
        availablePaths.has(normalizeSkillPath(path))
      )
    },

    clearSelectedSkills(): void {
      this.selectedSkillPaths = []
    },

    toggleSelectedSkill(skill: RuntimeSkillMetadata): void {
      const path = normalizeSkillPath(skill.path)
      if (!path) return

      if (this.selectedSkillPaths.map(normalizeSkillPath).includes(path)) {
        this.selectedSkillPaths = this.selectedSkillPaths.filter(
          (item) => normalizeSkillPath(item) !== path
        )
        return
      }

      this.selectedSkillPaths = [...this.selectedSkillPaths, path]
    },

    applySkillCatalog(skills: RuntimeSkillMetadata[], errors: RuntimeSkillLoadError[]): void {
      this.skills = skills || []
      this.skillErrors = errors || []
      this.skillsLoading = false
      this.pruneSelectedSkills()
    },

    applyInstallableSkills(
      skills: RuntimeInstallableSkill[],
      errors: RuntimeSkillRegistryError[]
    ): void {
      this.installableSkills = skills || []
      this.installableSkillErrors = errors || []
      this.installableSkillsLoading = false
    },

    async requestSkills(forceReload = false): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillsLoading = false
        this.errorMessage = '后端还没有连接，无法加载 Skills。'
        return
      }

      this.skillsLoading = true
      runtimeSocket.send({
        type: 'list_skills',
        request_id: `skills-${Date.now()}`,
        force_reload: forceReload
      })
    },

    async requestInstallableSkills(forceReload = false): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.installableSkillsLoading = false
        this.errorMessage = '后端还没有连接，无法加载可安装 Skills。'
        return
      }

      this.installableSkillsLoading = true
      runtimeSocket.send({
        type: 'list_installable_skills',
        request_id: 'installable-skills-' + Date.now(),
        force_reload: forceReload
      })
    },

    async loadSkillForEditing(skill: RuntimeSkillMetadata): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法读取 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillResourcesLoading = true
      this.skillResourceLoading = false
      this.skillResourcePath = ''
      this.skillResourceContent = ''
      this.skillResourceError = ''
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'get_skill',
        request_id: 'skill-load-' + Date.now(),
        path: skill.path
      })
      runtimeSocket.send({
        type: 'list_skill_resources',
        request_id: 'skill-resources-' + Date.now(),
        path: skill.path
      })
    },

    clearSkillEditor(): void {
      this.skillEditorLoading = false
      this.skillEditorSkill = null
      this.skillEditorContent = ''
      this.skillManagementError = ''
      this.skillResources = []
      this.skillResourcesLoading = false
      this.skillResourceLoading = false
      this.skillResourcePath = ''
      this.skillResourceContent = ''
      this.skillResourceError = ''
    },

    async requestSkillResources(skill: RuntimeSkillMetadata): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillResourceError = '后端还没有连接，无法加载 Skill 资源。'
        return
      }

      this.skillResourcesLoading = true
      this.skillResourceError = ''
      runtimeSocket.send({
        type: 'list_skill_resources',
        request_id: 'skill-resources-' + Date.now(),
        path: skill.path
      })
    },

    async loadSkillResource(skill: RuntimeSkillMetadata, resource: RuntimeSkillResource): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillResourceError = '后端还没有连接，无法读取 Skill 资源。'
        return
      }

      this.skillResourceLoading = true
      this.skillResourceError = ''
      runtimeSocket.send({
        type: 'get_skill_resource',
        request_id: 'skill-resource-load-' + Date.now(),
        path: skill.path,
        resource: resource.resource
      })
    },

    async saveSkillResource(draft: RuntimeSkillResourceDraft): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillResourceError = '后端还没有连接，无法保存 Skill 资源。'
        return
      }

      this.skillResourceLoading = true
      this.skillResourceError = ''
      this.skillResourcePath = draft.resource
      this.skillResourceContent = draft.content
      runtimeSocket.send({
        type: 'save_skill_resource',
        request_id: 'skill-resource-save-' + Date.now(),
        path: draft.skill_path,
        resource: draft.resource,
        content: draft.content
      })
    },

    async deleteSkillResource(skill: RuntimeSkillMetadata, resource: RuntimeSkillResource): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillResourceError = '后端还没有连接，无法删除 Skill 资源。'
        return
      }

      this.skillResourceLoading = true
      this.skillResourceError = ''
      runtimeSocket.send({
        type: 'delete_skill_resource',
        request_id: 'skill-resource-delete-' + Date.now(),
        path: skill.path,
        resource: resource.resource
      })
    },

    async createSkill(draft: RuntimeSkillDraft): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法创建 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'create_skill',
        request_id: 'skill-create-' + Date.now(),
        scope: draft.scope,
        name: draft.name,
        description: draft.description,
        short_description: draft.short_description,
        icon: draft.icon,
        allow_implicit_invocation: draft.allow_implicit_invocation,
        content: draft.content,
        package_template: draft.package_template
      })
    },

    async importSkill(draft: RuntimeSkillImportDraft): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法导入 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'import_skill',
        request_id: 'skill-import-' + Date.now(),
        scope: draft.scope,
        source_path: draft.source_path
      })
    },

    async installSkill(draft: RuntimeSkillInstallDraft): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法安装 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'install_skill',
        request_id: 'skill-install-' + Date.now(),
        scope: draft.scope,
        source_type: draft.source_type,
        source: draft.source
      })
    },

    async installRegistrySkill(skill: RuntimeInstallableSkill, scope: 'repo' | 'user' = 'user'): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法安装 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'install_registry_skill',
        request_id: 'skill-registry-install-' + Date.now(),
        scope,
        id: skill.id
      })
    },

    async reinstallSkill(skill: RuntimeSkillMetadata): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法重新安装 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'reinstall_skill',
        request_id: 'skill-reinstall-' + Date.now(),
        path: skill.path
      })
    },

    async reinstallRegistrySkill(skill: RuntimeInstallableSkill): Promise<void> {
      if (!skill.installed_path) return
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法重新安装 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'reinstall_skill',
        request_id: 'skill-registry-reinstall-' + Date.now(),
        path: skill.installed_path
      })
    },

    async updateSkill(draft: RuntimeSkillDraft): Promise<void> {
      if (!draft.path) return
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法更新 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'update_skill',
        request_id: 'skill-update-' + Date.now(),
        path: draft.path,
        name: draft.name,
        description: draft.description,
        short_description: draft.short_description,
        icon: draft.icon,
        allow_implicit_invocation: draft.allow_implicit_invocation,
        content: draft.content
      })
    },

    async deleteSkill(skill: RuntimeSkillMetadata): Promise<void> {
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.skillManagementError = '后端还没有连接，无法删除 Skill。'
        return
      }

      this.skillEditorLoading = true
      this.skillManagementError = ''
      runtimeSocket.send({
        type: 'delete_skill',
        request_id: 'skill-delete-' + Date.now(),
        path: skill.path
      })
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
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          this.skillsLoading = false
          this.installableSkillsLoading = false
          this.skillEditorLoading = false
          this.skillResourcesLoading = false
          this.skillResourceLoading = false
          if (!manualDisconnect) {
            this.scheduleReconnect()
          }
        },
        onError: (error) => {
          this.connectionStatus = 'error'
          this.errorMessage = error.message
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          this.skillsLoading = false
          this.installableSkillsLoading = false
          this.skillEditorLoading = false
          this.skillResourcesLoading = false
          this.skillResourceLoading = false
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

    isKnownProjectRoot(root: string): boolean {
      const normalizedRoot = normalizeSelectedRoot(root)
      return this.workspaceProjects.some(
        (project) => normalizeSelectedRoot(project.selected_root) === normalizedRoot
      )
    },

    isConversationWorkspace(root: string): boolean {
      const normalizedRoot = normalizeSelectedRoot(root)
      return isDefaultConversationSelectedRoot(normalizedRoot)
    },

    forgetConversationWorkspace(root: string): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      const nextRoots = this.conversationSelectedRoots.filter((item) => item !== normalizedRoot)
      if (nextRoots.length === this.conversationSelectedRoots.length) return

      this.conversationSelectedRoots = nextRoots
      this.persistConversationWorkspaces()
    },

    rememberConversationWorkspace(root: string): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      if (!normalizedRoot) return
      if (!isDefaultConversationSelectedRoot(normalizedRoot)) {
        this.forgetConversationWorkspace(normalizedRoot)
        return
      }

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

    reconcileSelectedRootAlias(aliasRoot?: string | null, canonicalRoot?: string | null): void {
      const alias = aliasRoot ? normalizeSelectedRoot(aliasRoot) : ''
      const canonical = canonicalRoot ? normalizeSelectedRoot(canonicalRoot) : ''
      if (!alias || !canonical || alias === canonical) return

      const aliasSessions = this.sessionsBySelectedRoot[alias] || []
      const canonicalSessions = this.sessionsBySelectedRoot[canonical] || []
      const remainingSessions = { ...this.sessionsBySelectedRoot }
      delete remainingSessions[alias]
      this.sessionsBySelectedRoot = {
        ...remainingSessions,
        [canonical]: mergeSessionSummaries(canonicalSessions, aliasSessions)
      }

      const canonicalIsKnownProject =
        !isDefaultConversationSelectedRoot(canonical) && this.isKnownProjectRoot(canonical)
      const isConversationAlias =
        !canonicalIsKnownProject &&
        (isDefaultConversationSelectedRoot(alias) || isDefaultConversationSelectedRoot(canonical))

      if (isConversationAlias) {
        this.conversationSelectedRoots = [
          canonical,
          ...this.conversationSelectedRoots.filter((item) => {
            const normalizedItem = normalizeSelectedRoot(item)
            return normalizedItem !== alias && normalizedItem !== canonical
          })
        ].slice(0, MAX_CONVERSATION_WORKSPACES)
      }

      this.workspaceProjects = this.workspaceProjects
        .map((project) =>
          normalizeSelectedRoot(project.selected_root) === alias
            ? { ...project, selected_root: canonical }
            : project
        )
        .filter((project, index, projects) => {
          const selectedRoot = normalizeSelectedRoot(project.selected_root)
          return (
            projects.findIndex(
              (item) => normalizeSelectedRoot(item.selected_root) === selectedRoot
            ) === index
          )
        })

      if (this.pendingConversationSelectedRoot === alias) {
        this.pendingConversationSelectedRoot = canonical
      }
      if (this.pendingWorkspaceResume?.selected_root === alias) {
        this.pendingWorkspaceResume = {
          ...this.pendingWorkspaceResume,
          selected_root: canonical
        }
      }

      this.persistConversationWorkspaces()
      this.persistWorkspaceProjects()
      try {
        window.localStorage.setItem(
          WORKSPACE_SESSIONS_STORAGE_KEY,
          JSON.stringify(this.sessionsBySelectedRoot)
        )
      } catch {
        // Session summaries are refreshed from the Python runtime when available.
      }
    },

    rememberWorkspace(workspace?: RuntimeWorkspace | null): void {
      if (!workspace) return

      const selectedRoot = normalizeSelectedRoot(workspace.selected_root)
      if (this.isConversationWorkspace(selectedRoot)) {
        this.rememberConversationWorkspace(selectedRoot)
        return
      }
      if (!isDefaultConversationSelectedRoot(selectedRoot)) {
        this.forgetConversationWorkspace(selectedRoot)
      }

      const project: RuntimeWorkspaceProject = {
        ...workspace,
        selected_root: selectedRoot,
        updated_at: Date.now()
      }
      const existingIndex = this.workspaceProjects.findIndex(
        (item) => normalizeSelectedRoot(item.selected_root) === selectedRoot
      )
      if (existingIndex === -1) {
        this.workspaceProjects = [...this.workspaceProjects, project].slice(
          0,
          MAX_WORKSPACE_PROJECTS
        )
      } else {
        this.workspaceProjects = this.workspaceProjects.map((item, index) =>
          index === existingIndex ? { ...item, ...project } : item
        )
      }
      this.persistWorkspaceProjects()
    },

    cacheWorkspaceSessions(root: string, sessions: RuntimeSessionSummary[]): void {
      const normalizedRoot = normalizeSelectedRoot(root)
      if (!normalizedRoot) return
      const rootSessions = sessions.filter((session) =>
        sessionBelongsToSelectedRoot(session, normalizedRoot)
      )

      this.sessionsBySelectedRoot = {
        ...this.sessionsBySelectedRoot,
        [normalizedRoot]: mergeSessionSummaries([], rootSessions)
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
      if (
        this.workspace?.selected_root &&
        normalizeSelectedRoot(this.workspace.selected_root) === normalizedRoot
      ) {
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
      const selectedSkills = this.getSelectedSkillPayload()
      runtimeSocket.send({
        type: 'user_input',
        content: text,
        model: this.selectedModel,
        reasoning_effort: this.reasoningEffort,
        ...(selectedSkills.length ? { selected_skills: selectedSkills } : {})
      })
      this.clearSelectedSkills()
    },

    applyPatchLifecycleEvent(event: PatchLifecycleEvent): void {
      if (
        event.type === 'patch_proposed' ||
        event.type === 'patch_approval_request' ||
        event.type === 'patch_apply_failed'
      ) {
        this.latestPatchResult = null
        this.pendingPatchReview = event
        return
      }

      if (this.pendingPatchReview?.patch_id === event.patch_id) {
        this.pendingPatchReview = null
      }
      this.latestPatchResult = latestPatchTestResult(event) ? event : null
    },

    async applyPatchReview(
      patchId?: string,
      selectedPaths?: string[],
      testCommand?: string,
      testTimeout?: number
    ): Promise<void> {
      const cleanPatchId = (patchId || this.pendingPatchReview?.patch_id || '').trim()
      if (!cleanPatchId) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法应用 Patch。'
        return
      }

      const turnId =
        this.pendingPatchReview?.patch_id === cleanPatchId ? this.pendingPatchReview.turn_id : ''
      runtimeSocket.send({
        type: 'apply_patch_review',
        request_id: 'patch-apply-' + Date.now(),
        patch_id: cleanPatchId,
        ...(selectedPaths ? { selected_paths: normalizePatchSelection(selectedPaths) } : {}),
        ...(testCommand?.trim() ? { test_command: testCommand.trim() } : {}),
        ...(typeof testTimeout === 'number' ? { test_timeout: testTimeout } : {}),
        ...(turnId ? { turn_id: turnId } : {})
      })
    },

    async rejectPatchReview(patchId?: string): Promise<void> {
      const cleanPatchId = (patchId || this.pendingPatchReview?.patch_id || '').trim()
      if (!cleanPatchId) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法拒绝 Patch。'
        return
      }

      const turnId =
        this.pendingPatchReview?.patch_id === cleanPatchId ? this.pendingPatchReview.turn_id : ''
      runtimeSocket.send({
        type: 'reject_patch_review',
        request_id: 'patch-reject-' + Date.now(),
        patch_id: cleanPatchId,
        reason: '用户在 Diff Review 卡片中拒绝',
        ...(turnId ? { turn_id: turnId } : {})
      })
    },

    dismissPatchReview(patchId?: string): void {
      if (!patchId || this.pendingPatchReview?.patch_id === patchId) {
        this.pendingPatchReview = null
      }
    },

    dismissLatestPatchResult(): void {
      this.latestPatchResult = null
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

    approvePermission(scope: 'once' | 'session' = 'once'): void {
      this.sendPermissionDecision(true, undefined, scope)
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

      const requestId = `resume-${Date.now()}`
      this.pendingResumeRequestId = requestId
      this.pendingResumeRequestRoot = this.getActiveWorkspaceRoot()
      this.pendingResumeSessionId = sessionId || this.sessionId
      if (sessionId) {
        this.setSelectedSession(sessionId, this.pendingResumeRequestRoot)
      }

      runtimeSocket?.send({
        type: 'resume_session',
        request_id: requestId,
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
      this.setSelectedSession('', this.getActiveWorkspaceRoot())
      this.activeTurnId = ''
      this.activePermission = null
      this.activeClarification = null
      this.pendingPlan = null
      this.pendingPatchReview = null
      this.latestPatchResult = null
      this.planRequestInFlight = false
      this.pendingPlanRequestId = ''
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
      if (
        this.workspace?.selected_root &&
        normalizeSelectedRoot(this.workspace.selected_root) === root
      ) {
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
      if (
        this.workspace?.selected_root &&
        normalizeSelectedRoot(this.workspace.selected_root) === root
      ) {
        await this.resumeSession(sessionId)
        return
      }

      this.pendingWorkspaceResume = { selected_root: root, sessionId }
      this.setSelectedSession(sessionId, root)
      await this.openWorkspace(path)
    },

    async openWorkspace(path: string): Promise<void> {
      const root = normalizeSelectedRoot(path)
      const requestId = `workspace-${Date.now()}`
      this.pendingWorkspaceRequestId = requestId
      this.pendingWorkspaceRequestRoot = root
      this.invalidatePendingResume(root)
      this.setSelectedSession('', root)
      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.pendingWorkspaceResume = null
        this.pendingConversationSelectedRoot = null
        this.pendingWorkspaceRequestId = ''
        this.pendingWorkspaceRequestRoot = ''
        this.errorMessage = '后端还没有连接，无法打开工作区。'
        return
      }

      runtimeSocket.send({
        type: 'open_workspace',
        request_id: requestId,
        path
      })
    },

    async changeDirectory(path: string): Promise<void> {
      const target = path.trim()
      if (!target) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法切换当前目录。'
        return
      }

      runtimeSocket.send({
        type: 'change_directory',
        request_id: `change-directory-${Date.now()}`,
        path: target
      })
    },

    async addWorkspaceDirectory(path: string, access: 'read' | 'write'): Promise<void> {
      const target = path.trim()
      if (!target) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法加入额外目录。'
        return
      }

      runtimeSocket.send({
        type: 'add_dir',
        request_id: `add-dir-${Date.now()}`,
        path: target,
        access
      })
    },

    async trustWorkspace(): Promise<void> {
      await this.setWorkspaceTrust(true)
    },

    async untrustWorkspace(): Promise<void> {
      await this.setWorkspaceTrust(false)
    },

    async setWorkspaceTrust(trusted: boolean): Promise<void> {
      if (!this.workspace) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法更新工作区信任状态。'
        return
      }

      runtimeSocket.send({
        type: trusted ? 'trust_workspace' : 'untrust_workspace',
        request_id: `${trusted ? 'trust' : 'untrust'}-workspace-${Date.now()}`
      })
    },

    async setPermissionMode(mode: RuntimePermissionMode): Promise<void> {
      const normalized = normalizePermissionMode(mode)
      if (!normalized) return

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法切换权限模式。'
        return
      }

      runtimeSocket.send({
        type: 'set_permission_mode',
        request_id: `permission-mode-${Date.now()}`,
        mode: normalized
      })
    },

    syncGlobalPermissionMode(): void {
      if (!runtimeSocket?.isOpen || !this.workspace) return

      const activeMode = this.workspace.policy?.permission_mode || DEFAULT_PERMISSION_MODE
      if (activeMode === this.globalPermissionMode) return

      runtimeSocket.send({
        type: 'set_permission_mode',
        request_id: `permission-mode-sync-${Date.now()}`,
        mode: this.globalPermissionMode
      })
    },

    cancelTurn(): void {
      if (!runtimeSocket?.isOpen) {
        this.errorMessage = '后端还没有连接，无法取消当前回合。'
        return
      }

      runtimeSocket.send({
        type: 'cancel_turn',
        request_id: `cancel-turn-${Date.now()}`,
        turn_id: this.activeTurnId || this.sessionState?.active_turn_id || undefined
      })
    },

    sendPermissionDecision(
      approved: boolean,
      feedback?: string,
      scope: 'once' | 'session' = 'once'
    ): void {
      if (!this.activePermission || !runtimeSocket?.isOpen) return

      const suggestedPrefix = this.activePermission.metadata.suggested_prefix_rule
      const prefixRule =
        scope === 'session' &&
        Array.isArray(suggestedPrefix) &&
        suggestedPrefix.every((item) => typeof item === 'string' && item.trim())
          ? suggestedPrefix.map((item) => item.trim())
          : undefined

      runtimeSocket.send({
        type: 'permission_decision',
        request_id: this.activePermission.request_id,
        approved,
        feedback,
        scope,
        prefix_rule: prefixRule
      })
    },

    undoFile(filePath: string): void {
      if (!runtimeSocket?.isOpen) return

      runtimeSocket.send({
        type: 'undo_file',
        request_id: `undo-file-${Date.now()}`,
        file_path: filePath
      })
    },

    async requestPlan(content: string): Promise<void> {
      const cleanContent = content.trim()
      if (!cleanContent || this.planRequestInFlight) return

      const chat = useChatStore()
      this.planRequestInFlight = true
      this.pendingPlanRequestId = `plan-${Date.now()}`

      if (!runtimeSocket?.isOpen) {
        await this.connect({ silent: true })
      }

      if (!runtimeSocket?.isOpen) {
        this.planRequestInFlight = false
        this.pendingPlanRequestId = ''
        chat.addSystemMessage('后端还没有连接，先启动 Python WebSocket 服务后再生成计划。')
        return
      }

      chat.addUserMessage(cleanContent)
      chat.setFallbackConversationTitle()
      chat.addSystemMessage('正在生成计划...')

      runtimeSocket.send({
        type: 'plan_request',
        request_id: this.pendingPlanRequestId,
        content: cleanContent
      })
    },

    confirmPlan(planId?: string): void {
      if (!runtimeSocket?.isOpen) return

      const selectedSkills = this.getSelectedSkillPayload()
      runtimeSocket.send({
        type: 'plan_confirm',
        request_id: `plan-confirm-${Date.now()}`,
        plan_id: planId,
        ...(selectedSkills.length ? { selected_skills: selectedSkills } : {})
      })
      this.clearSelectedSkills()
    },

    cancelPlan(planId?: string): void {
      if (!runtimeSocket?.isOpen) return

      runtimeSocket.send({
        type: 'plan_cancel',
        request_id: `plan-cancel-${Date.now()}`,
        plan_id: planId
      })
      this.clearSelectedSkills()
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
          this.schemaVersion = event.schema_version
          this.tools = event.tools
          this.sessionState = event.session_state
          this.workspace = event.workspace || null
          this.setSelectedSession(event.session_id, this.getActiveWorkspaceRoot())
          this.applyModelConfig(event.model_config)
          this.rememberWorkspace(this.workspace)
          if (this.workspace?.selected_root) {
            this.sessionHistory =
              this.sessionsBySelectedRoot[normalizeSelectedRoot(this.workspace.selected_root)] || []
          }
          chat.addSystemMessage(`已连接后端：${event.session_id}`)
          this.syncGlobalPermissionMode()
          this.requestSessions()
          void this.requestSkills()
          this.requestConversationTitle()
          break
        case 'workspace_changed':
          if (
            this.pendingWorkspaceRequestId &&
            event.request_id &&
            event.request_id !== this.pendingWorkspaceRequestId
          ) {
            break
          }
          if (
            this.pendingWorkspaceRequestRoot &&
            normalizeSelectedRoot(event.workspace.selected_root) !==
              this.pendingWorkspaceRequestRoot
          ) {
            break
          }
          this.pendingWorkspaceRequestId = ''
          this.pendingWorkspaceRequestRoot = ''
          this.sessionId = event.session_id
          this.sessionState = event.session_state
          this.workspace = event.workspace
          this.setSelectedSession(event.session_id, event.workspace.selected_root)
          this.reconcileSelectedRootAlias(
            this.pendingWorkspaceResume?.selected_root || this.pendingConversationSelectedRoot,
            event.workspace.selected_root
          )
          this.rememberWorkspace(event.workspace)
          this.tools = event.tools
          this.activeTurnId = ''
          this.activePermission = null
          this.activeClarification = null
          this.pendingPlan = null
          this.pendingPatchReview = null
          this.latestPatchResult = null
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          this.sessionHistory =
            this.sessionsBySelectedRoot[normalizeSelectedRoot(event.workspace.selected_root)] || []
          chat.resetConversation()
          chat.addSystemMessage(`已打开工作区：${event.workspace.display_name}`)
          this.clearSelectedSkills()
          this.clearSkillEditor()
          this.skills = []
          this.skillErrors = []
          this.requestSessions()
          void this.requestSkills(true)
          if (
            this.pendingConversationSelectedRoot ===
            normalizeSelectedRoot(event.workspace.selected_root)
          ) {
            this.pendingConversationSelectedRoot = null
          }
          if (
            this.pendingWorkspaceResume?.selected_root ===
            normalizeSelectedRoot(event.workspace.selected_root)
          ) {
            const sessionId = this.pendingWorkspaceResume.sessionId
            this.pendingWorkspaceResume = null
            void this.resumeSession(sessionId)
          }
          this.syncGlobalPermissionMode()
          break
        case 'workspace_policy_changed':
          this.sessionState = event.session_state
          this.workspace = event.workspace
          this.rememberWorkspace(event.workspace)
          void this.requestSkills(true)
          if (event.reason === 'change_directory') {
            chat.addSystemMessage(
              `当前目录已切换：${formatWorkspacePath(event.workspace, event.workspace.current_dir)}`
            )
          } else if (event.reason === 'add_dir' && event.added_root) {
            chat.addSystemMessage(
              `已加入额外目录：${event.added_root.access === 'write' ? '可写' : '只读'} ${event.added_root.path}`
            )
          } else if (event.reason === 'trust_workspace') {
            chat.addSystemMessage('已信任当前项目：后端会读取白名单内的项目策略配置。')
          } else if (event.reason === 'untrust_workspace') {
            chat.addSystemMessage('已取消信任当前项目：后端将忽略项目本地策略配置。')
          } else if (event.reason === 'set_permission_mode') {
            const nextMode = normalizePermissionMode(
              event.permission_mode || event.workspace.policy?.permission_mode
            )
            if (nextMode) {
              this.globalPermissionMode = nextMode
              saveGlobalPermissionMode(nextMode)
            }
            if (!String(event.request_id || '').startsWith('permission-mode-sync-')) {
              chat.addSystemMessage(
                `权限模式已切换：${formatPermissionModeLabel(event.workspace.policy?.permission_mode)}`
              )
            }
          }
          break
        case 'turn_started':
          this.activeTurnId = event.turn_id
          this.sessionState = event.session_state
          this.pendingPlan = null
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          chat.startActivity(event.turn_id)
          break
        case 'plan_pending':
          this.sessionState = event.session_state
          this.pendingPlan = event
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          if (event.plan_markdown?.trim()) {
            chat.addAssistantMessage(event.plan_markdown.trim(), '计划书')
          }
          chat.addSystemMessage(`计划已生成：${event.items.length} 个步骤，等待确认。`)
          break
        case 'plan_cancelled':
          this.sessionState = event.session_state
          if (!this.pendingPlan || this.pendingPlan.plan_id === event.plan_id) {
            this.pendingPlan = null
          }
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          chat.addSystemMessage('计划已取消。')
          break
        case 'task_list':
          chat.setActiveTaskList(event.items || [], event.turn_id || this.activeTurnId)
          break
        case 'file_undone':
          chat.markChangedFileUndone(event.file_path)
          break
        case 'session_created':
          this.sessionId = event.session_id
          this.sessionState = event.session_state
          this.workspace = event.workspace || this.workspace
          this.setSelectedSession(event.session_id, this.getActiveWorkspaceRoot())
          this.pendingResumeRequestId = ''
          this.pendingResumeRequestRoot = ''
          this.pendingResumeSessionId = ''
          this.rememberWorkspace(this.workspace)
          if (this.workspace?.selected_root) {
            this.sessionHistory =
              this.sessionsBySelectedRoot[normalizeSelectedRoot(this.workspace.selected_root)] || []
          }
          this.activeTurnId = ''
          this.activePermission = null
          this.activeClarification = null
          this.pendingPlan = null
          this.pendingPatchReview = null
          this.latestPatchResult = null
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          this.clearSkillEditor()
          chat.resetConversation()
          this.syncGlobalPermissionMode()
          this.requestSessions()
          void this.requestSkills()
          break
        case 'sessions_list':
          if (
            !event.workspace?.selected_root ||
            normalizeSelectedRoot(event.workspace.selected_root) ===
              (this.workspace?.selected_root
                ? normalizeSelectedRoot(this.workspace.selected_root)
                : '')
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
              (this.workspace?.selected_root
                ? normalizeSelectedRoot(this.workspace.selected_root)
                : '')
            ) {
              this.sessionHistory = event.sessions
            }
          }
          if (event.deleted_current) {
            this.sessionId = event.session_id || this.sessionId
            this.setSelectedSession(
              event.session_id || this.sessionId,
              this.getActiveWorkspaceRoot()
            )
            this.sessionState = event.session_state
            this.activeTurnId = ''
            this.activePermission = null
            this.activeClarification = null
            this.pendingPlan = null
            this.pendingPatchReview = null
            this.latestPatchResult = null
            this.planRequestInFlight = false
            this.pendingPlanRequestId = ''
            chat.resetConversation()
          } else if (this.selectedSessionId === event.deleted_session_id) {
            this.setSelectedSession(this.sessionId, this.getActiveWorkspaceRoot())
          }
          break
        case 'assistant_token':
          chat.appendAssistantToken(event.token)
          break
        case 'tool_call_started': {
          const turnId = event.turn_id || this.activeTurnId
          const toolTaskList = extractTaskListFromToolArguments(event.name, event.arguments)
          if (toolTaskList.length) {
            chat.setActiveTaskList(toolTaskList, turnId)
          }
          chat.upsertActivityEvent(formatToolStartLabel(event.name, event.arguments), 'running', {
            requestId: event.request_id,
            kind: getToolKind(event.name),
            detail: formatToolDetail(event.arguments),
            toolName: event.name,
            turnId
          })
          break
        }
        case 'tool_call_result': {
          const turnId = event.turn_id || this.activeTurnId
          const toolTaskList = event.ok
            ? extractTaskListFromToolResult(event.name, event.content)
            : []
          if (toolTaskList.length) {
            chat.setActiveTaskList(toolTaskList, turnId)
          }
          chat.upsertActivityEvent(
            formatToolResultLabel(event.name, event.ok),
            event.ok ? 'success' : 'error',
            {
              requestId: event.request_id,
              kind: getToolKind(event.name),
              detail: event.content,
              toolName: event.name,
              turnId
            }
          )
          break
        }
        case 'patch_proposed':
        case 'patch_approval_request':
        case 'patch_applied':
        case 'patch_rejected':
        case 'patch_apply_failed':
        case 'patch_rolled_back': {
          const turnId = event.turn_id || this.activeTurnId
          this.applyPatchLifecycleEvent(event)
          chat.upsertActivityEvent(formatPatchLifecycleLabel(event), patchLifecycleStatus(event), {
            requestId: event.request_id,
            kind: event.type === 'patch_approval_request' ? 'permission' : 'edit',
            detail: formatPatchLifecycleDetail(event),
            toolName: event.tool || 'patch',
            turnId
          })
          const systemMessage = formatPatchSystemMessage(event)
          if (systemMessage) {
            chat.addSystemMessage(systemMessage)
          }
          break
        }
        case 'permission_request':
          this.activePermission = event
          this.activeClarification = null
          chat.upsertActivityEvent(`等待权限确认：${formatToolLabel(event)}`, 'waiting', {
            requestId: event.request_id,
            kind: 'permission',
            detail: event.detail,
            toolName: event.tool,
            turnId: event.turn_id || this.activeTurnId
          })
          break
        case 'permission_decision_ack': {
          const tool = this.activePermission?.tool
          this.activePermission = null
          chat.upsertActivityEvent(
            event.approved ? '权限已允许' : '权限已拒绝',
            event.approved ? 'success' : 'error',
            {
              requestId: event.request_id,
              kind: 'permission',
              toolName: tool,
              turnId: event.turn_id || this.activeTurnId
            }
          )
          break
        }
        case 'clarification_request':
          this.activeClarification = event
          this.activePermission = null
          chat.upsertActivityEvent(`正在询问：${formatClarificationLabel(event)}`, 'waiting', {
            requestId: event.request_id,
            kind: 'question',
            detail: formatClarificationDetail(event),
            turnId: event.turn_id || this.activeTurnId
          })
          break
        case 'clarification_response_ack':
          this.activeClarification = null
          chat.upsertActivityEvent(event.skipped ? '已跳过问题' : '已收到回答', 'success', {
            requestId: event.request_id,
            kind: 'question',
            turnId: event.turn_id || this.activeTurnId
          })
          break
        case 'session_suspended':
        case 'session_blocked':
        case 'session_busy':
          this.sessionState = event.session_state
          chat.addSystemMessage(event.detail || event.type)
          break
        case 'turn_cancelling':
          this.sessionState = event.session_state
          chat.addSystemMessage('正在取消当前回合...')
          break
        case 'turn_cancelled':
          {
            const turnId = event.turn_id || this.activeTurnId
            this.sessionState = event.session_state
            this.activeTurnId = ''
            this.activePermission = null
            this.activeClarification = null
            chat.addSystemMessage(event.detail || '当前回合已取消。')
            chat.finishActivity('error', turnId)
          }
          break
        case 'session_resumed':
          if (
            this.pendingResumeRequestId &&
            event.request_id &&
            event.request_id !== this.pendingResumeRequestId
          ) {
            break
          }
          if (
            this.pendingResumeRequestRoot &&
            event.workspace?.selected_root &&
            normalizeSelectedRoot(event.workspace.selected_root) !== this.pendingResumeRequestRoot
          ) {
            break
          }
          this.pendingResumeRequestId = ''
          this.pendingResumeRequestRoot = ''
          this.pendingResumeSessionId = ''
          if (event.session_id) {
            this.sessionId = event.session_id
          }
          this.sessionState = event.session_state
          if (event.workspace) {
            this.reconcileSelectedRootAlias(
              this.pendingWorkspaceResume?.selected_root || this.pendingConversationSelectedRoot,
              event.workspace.selected_root
            )
            this.workspace = event.workspace
            this.rememberWorkspace(event.workspace)
          }
          this.setSelectedSession(event.session_id || this.sessionId, this.getActiveWorkspaceRoot())
          this.activeClarification = null
          this.pendingPlan = null
          this.pendingPatchReview = event.pending_patch_review || null
          this.planRequestInFlight = false
          this.pendingPlanRequestId = ''
          if (event.resumed_from_disk) {
            chat.loadConversation(event.messages || [], event.session?.title || '历史会话')
          } else {
            chat.addSystemMessage(event.detail || '会话已恢复。')
          }
          this.syncGlobalPermissionMode()
          this.requestSessions()
          void this.requestSkills(true)
          this.requestConversationTitle()
          break
        case 'skills_listed':
          if (
            event.workspace?.selected_root &&
            this.workspace?.selected_root &&
            normalizeSelectedRoot(event.workspace.selected_root) !==
              normalizeSelectedRoot(this.workspace.selected_root)
          ) {
            break
          }
          this.applySkillCatalog(event.skills || [], event.errors || [])
          if (event.errors?.length) {
            chat.addSystemMessage(`Skills 加载提示：${event.errors.length} 个条目未加载。`)
          }
          break
        case 'installable_skills_listed':
          if (
            event.workspace?.selected_root &&
            this.workspace?.selected_root &&
            normalizeSelectedRoot(event.workspace.selected_root) !==
              normalizeSelectedRoot(this.workspace.selected_root)
          ) {
            break
          }
          this.applyInstallableSkills(event.installable_skills || [], event.errors || [])
          if (event.errors?.length) {
            chat.addSystemMessage('可安装 Skills 加载提示：' + event.errors.length + ' 个条目未加载。')
          }
          break
        case 'skill_loaded':
          if (
            event.workspace?.selected_root &&
            this.workspace?.selected_root &&
            normalizeSelectedRoot(event.workspace.selected_root) !==
              normalizeSelectedRoot(this.workspace.selected_root)
          ) {
            break
          }
          this.skillEditorLoading = false
          this.skillEditorSkill = event.skill
          this.skillEditorContent = event.content || ''
          this.skillManagementError = ''
          break
        case 'skill_saved':
          this.applySkillCatalog(event.skills || [], event.errors || [])
          this.skillEditorLoading = false
          this.skillEditorSkill = null
          this.skillEditorContent = ''
          this.skillManagementError = ''
          this.skillResources = []
          this.skillResourcesLoading = false
          this.skillResourceLoading = false
          this.skillResourcePath = ''
          this.skillResourceContent = ''
          this.skillResourceError = ''
          chat.addSystemMessage(
            event.action === 'create'
              ? 'Skill 已创建。'
              : event.action === 'import'
                ? 'Skill 已导入。'
                : event.action === 'install'
                  ? 'Skill 已安装。'
                  : event.action === 'install_registry'
                    ? 'Skill 已安装。'
                    : event.action === 'reinstall'
                      ? 'Skill 已重新安装。'
                      : 'Skill 已更新。'
          )
          if (
            event.action === 'install' ||
            event.action === 'install_registry' ||
            event.action === 'reinstall' ||
            event.action === 'update'
          ) {
            void this.requestInstallableSkills(true)
          }
          break
        case 'skill_deleted':
          this.applySkillCatalog(event.skills || [], event.errors || [])
          this.skillEditorLoading = false
          if (
            this.skillEditorSkill?.path &&
            normalizeSkillPath(this.skillEditorSkill.path) === normalizeSkillPath(event.skill.path)
          ) {
            this.skillEditorSkill = null
            this.skillEditorContent = ''
            this.skillResources = []
            this.skillResourcePath = ''
            this.skillResourceContent = ''
          }
          this.selectedSkillPaths = this.selectedSkillPaths.filter(
            (path) => normalizeSkillPath(path) !== normalizeSkillPath(event.skill.path)
          )
          this.skillManagementError = ''
          chat.addSystemMessage('Skill 已删除。')
          void this.requestInstallableSkills(true)
          break
        case 'skill_resources_listed':
          if (
            event.workspace?.selected_root &&
            this.workspace?.selected_root &&
            normalizeSelectedRoot(event.workspace.selected_root) !==
              normalizeSelectedRoot(this.workspace.selected_root)
          ) {
            break
          }
          this.skillResources = event.resources || []
          this.skillResourcesLoading = false
          this.skillResourceError = ''
          break
        case 'skill_resource_loaded':
          this.skillResourceLoading = false
          this.skillResourcePath = event.resource.resource
          this.skillResourceContent = event.content || ''
          this.skillResourceError = ''
          break
        case 'skill_resource_saved':
          this.skillResources = event.resources || []
          this.skillResourceLoading = false
          this.skillResourcePath = event.resource.resource
          this.skillResourceError = ''
          chat.addSystemMessage('Skill 资源已保存。')
          break
        case 'skill_resource_deleted':
          this.skillResources = event.resources || []
          this.skillResourceLoading = false
          if (this.skillResourcePath === event.resource.resource) {
            this.skillResourcePath = ''
            this.skillResourceContent = ''
          }
          this.skillResourceError = ''
          chat.addSystemMessage('Skill 资源已删除。')
          break
        case 'skill_error':
          this.skillEditorLoading = false
          this.skillResourcesLoading = false
          this.skillResourceLoading = false
          this.skillManagementError = event.message
          this.skillResourceError = event.message
          chat.addSystemMessage(`Skill 错误：${event.message}`)
          break
        case 'skill_used':
          chat.upsertActivityEvent(
            `已使用 Skill：${event.name || event.path}`,
            'success',
            {
              requestId: event.request_id,
              kind: 'skill',
              detail: event.path,
              toolName: 'skill',
              turnId: event.turn_id || this.activeTurnId
            }
          )
          break
        case 'skill_warning':
          chat.addSystemMessage(`Skill 提醒：${event.message}`)
          break
        case 'final_answer':
          {
            const turnId = event.turn_id || this.activeTurnId
            this.sessionState = event.session_state
            this.activeTurnId = ''
            this.activeClarification = null
            chat.finishActivity('success', turnId)
            chat.finishAssistantStream(event.content, event.changed_files)
            this.requestSessions()
            this.requestConversationTitle()
          }
          break
        case 'conversation_title':
          chat.finishConversationTitleRequest(event.title, event.request_id)
          this.requestSessions()
          break
        case 'error':
        case 'workspace_error':
          this.errorMessage = event.message
          if (
            this.pendingPlanRequestId &&
            event.request_id &&
            event.request_id === this.pendingPlanRequestId
          ) {
            this.planRequestInFlight = false
            this.pendingPlanRequestId = ''
          }
          if (event.type === 'workspace_error') {
            if (
              this.pendingWorkspaceRequestId &&
              event.request_id &&
              event.request_id !== this.pendingWorkspaceRequestId
            ) {
              break
            }
            if (
              this.pendingWorkspaceRequestRoot &&
              event.requested_workspace &&
              normalizeSelectedRoot(event.requested_workspace) !== this.pendingWorkspaceRequestRoot
            ) {
              break
            }
            this.pendingWorkspaceResume = null
            this.pendingConversationSelectedRoot = null
            this.pendingWorkspaceRequestId = ''
            this.pendingWorkspaceRequestRoot = ''
            if (event.workspace) {
              this.workspace = event.workspace
              this.setSelectedSession(this.sessionId, event.workspace.selected_root)
            } else {
              this.setSelectedSession(this.sessionId, this.getActiveWorkspaceRoot())
            }
            if (event.requested_permission_mode) {
              const activeMode = normalizePermissionMode(event.workspace?.policy?.permission_mode)
              if (activeMode) {
                this.globalPermissionMode = activeMode
                saveGlobalPermissionMode(activeMode)
              }
            }
          }
          if (event.request_id?.startsWith('sessions-')) {
            this.sessionsLoading = false
          }
          if (event.request_id?.startsWith('skills-')) {
            this.skillsLoading = false
          }
          if (event.request_id?.startsWith('installable-skills-')) {
            this.installableSkillsLoading = false
          }
          if (event.request_id?.startsWith('skill-')) {
            this.skillEditorLoading = false
            this.skillManagementError = event.message
          }
          if (event.request_id?.startsWith('title-')) {
            chat.failConversationTitleRequest(event.request_id)
            break
          }
          chat.upsertActivityEvent('后端错误', 'error', {
            requestId: event.request_id,
            kind: 'error',
            detail: event.message,
            turnId: event.turn_id || this.activeTurnId
          })
          chat.finishActivity('error', event.turn_id || this.activeTurnId)
          break
      }
    }
  }
})
