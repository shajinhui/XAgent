<script setup lang="ts">
import { nextTick, ref, watch, type Component } from 'vue'
import {
  Blocks,
  BrainCircuit,
  ChevronRight,
  Copy,
  FileText,
  Globe,
  MessageCircleQuestion,
  PencilLine,
  Search,
  ShieldAlert,
  Terminal,
  TriangleAlert,
  Wrench
} from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
import { renderMarkdown } from '@renderer/services/markdown'
import { useChatStore } from '@renderer/stores/chat'
import { useRuntimeStore } from '@renderer/stores/runtime'
import type { ActivityStepKind, ChatMessage } from '@renderer/stores/chat'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const transcript = ref<HTMLElement | null>(null)
const chat = useChatStore()
const runtime = useRuntimeStore()
const expandedFileGroups = ref<Set<number>>(new Set())
const expandedActivitySections = ref<Set<string>>(new Set())
const expandedCommandOutputs = ref<Set<number>>(new Set())

// 交错轨迹块: 文本段落和工具组按时间顺序交替排列
type ActivityTraceBlock =
  | { type: 'text'; content: string; messageId: number }
  | { type: 'tools'; key: string; kind: ActivityStepKind; title: string; events: ChatMessage[] }

const activityIcons: Record<ActivityStepKind, Component> = {
  thinking: BrainCircuit,
  search: Search,
  read: FileText,
  edit: PencilLine,
  command: Terminal,
  permission: ShieldAlert,
  question: MessageCircleQuestion,
  web: Globe,
  skill: Blocks,
  tool: Wrench,
  error: TriangleAlert
}

function getActivityIcon(kind: ActivityStepKind): Component {
  return activityIcons[kind] || activityIcons.tool
}

function isMessageHidden(message: ChatMessage): boolean {
  // 隐藏所有 activity_event（它们通过 activity trace blocks 展示）
  if (message.role === 'activity_event') {
    return true
  }

  // 活动组内的非final消息始终隐藏（中间思考文本在 trace 块内渲染）
  if (message.activityGroupId && !message.isFinal) {
    return true
  }

  return false
}

function isAssistantActivityResult(message: ChatMessage): boolean {
  return (
    message.role === 'assistant' && Boolean(message.activityGroupId) && Boolean(message.isFinal)
  )
}

function getUniquePathCount(events: ChatMessage[]): number {
  const paths = new Set<string>()
  events.forEach((event) => {
    const path = formatPathLabel(parseStepInput(event).path)
    if (path) paths.add(path)
  })
  return paths.size || events.length
}

// 为一组连续工具事件生成合并标题
function getToolBlockTitle(events: ChatMessage[]): string {
  const parts: string[] = []
  const readEvents = events.filter((e) => e.step?.kind === 'read')
  const searchEvents = events.filter((e) => e.step?.kind === 'search')
  const editEvents = events.filter((e) => e.step?.kind === 'edit')
  const commandEvents = events.filter((e) => e.step?.kind === 'command')
  const webEvents = events.filter((e) => e.step?.kind === 'web')
  const skillEvents = events.filter((e) => e.step?.kind === 'skill')

  if (readEvents.length) parts.push(`读取 ${readEvents.length} 个文件`)
  if (searchEvents.length)
    parts.push(searchEvents.length > 1 ? `搜索 ${searchEvents.length} 次代码` : '搜索代码')
  if (editEvents.length) parts.push(`已编辑 ${getUniquePathCount(editEvents)} 个文件`)
  if (commandEvents.length === 1) {
    parts.push(formatCommandSummary(commandEvents[0]))
  } else if (commandEvents.length > 1) {
    parts.push(`已运行 ${commandEvents.length} 条命令`)
  }
  if (webEvents.length) parts.push(`已获取 ${webEvents.length} 个网页`)
  if (skillEvents.length) {
    parts.push(skillEvents.length > 1 ? `已使用 ${skillEvents.length} 个 Skills` : '已使用 Skill')
  }

  const otherCount = events.filter((e) => {
    const k = e.step?.kind
    return k && !['read', 'search', 'edit', 'command', 'web', 'skill'].includes(k)
  }).length
  if (otherCount) parts.push(`已调用 ${otherCount} 个工具`)

  return parts.join('，') || '使用工具'
}

// 选取工具块的主图标
function getToolBlockPrimaryKind(events: ChatMessage[]): ActivityStepKind {
  const kindPriority: ActivityStepKind[] = ['edit', 'command', 'web', 'skill', 'search', 'read']
  for (const kind of kindPriority) {
    if (events.some((e) => e.step?.kind === kind)) return kind
  }
  return events[0]?.step?.kind || 'tool'
}

// 按时间顺序构建交错的轨迹块: 文本和工具组交替排列
function getActivityTraceBlocks(groupId?: number): ActivityTraceBlock[] {
  if (!groupId) return []

  // 收集活动组内所有消息（activity_event + 非final assistant），保持原始顺序
  const groupMessages = props.messages.filter(
    (m) =>
      m.activityGroupId === groupId &&
      ((m.role === 'activity_event' && m.step) || (m.role === 'assistant' && !m.isFinal))
  )

  const blocks: ActivityTraceBlock[] = []
  let pendingToolEvents: ChatMessage[] = []
  let blockIndex = 0

  function flushTools(): void {
    if (!pendingToolEvents.length) return
    const kind = getToolBlockPrimaryKind(pendingToolEvents)
    blocks.push({
      type: 'tools',
      key: `${groupId}:block:${blockIndex++}`,
      kind,
      title: getToolBlockTitle(pendingToolEvents),
      events: [...pendingToolEvents]
    })
    pendingToolEvents = []
  }

  for (const msg of groupMessages) {
    if (msg.role === 'activity_event') {
      pendingToolEvents.push(msg)
    } else {
      // assistant 文本 — 先刷出累积的工具事件
      flushTools()
      const text = msg.content.trim()
      if (text) {
        blocks.push({ type: 'text', content: text, messageId: msg.id })
      }
    }
  }
  flushTools()

  return blocks
}

function parseStepInput(message: ChatMessage): Record<string, unknown> {
  const rawValue = message.step?.inputDetail || message.step?.detail
  if (!rawValue) return {}

  try {
    const parsed = JSON.parse(rawValue)
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}

function formatPathLabel(path: unknown): string {
  if (typeof path !== 'string' || !path.trim()) return ''
  return path.trim().replace(/^\.\/+/, '')
}

function formatActivityTraceLine(message: ChatMessage): string {
  const step = message.step
  if (!step) return message.content

  const input = parseStepInput(message)
  const path = formatPathLabel(input.path)
  if (step.kind === 'read') return path ? `读取 ${path}` : step.startedLabel || step.label
  if (step.kind === 'search') {
    const pattern = typeof input.pattern === 'string' ? input.pattern.trim() : ''
    const searchPath = path && path !== '.' ? `（${path}）` : ''
    return pattern ? `搜索 ${pattern}${searchPath}` : step.startedLabel || step.label
  }
  if (step.kind === 'edit') return path ? `编辑 ${path}` : step.startedLabel || step.label
  if (step.kind === 'command') {
    const command = typeof input.command === 'string' ? input.command.trim() : ''
    return command ? `运行 ${command}` : step.startedLabel || step.label
  }
  if (step.kind === 'web') {
    const url = typeof input.url === 'string' ? input.url.trim() : ''
    return url ? `获取 ${url}` : step.startedLabel || step.label
  }
  // 其他工具类型: 优先取 label（最终标签），避免 startedLabel 带 "正在调用" 前缀导致重复
  const toolLabel = step.label || step.startedLabel || message.content
  if (/调用/.test(toolLabel)) return toolLabel
  return `调用 ${toolLabel}`
}

function formatActivityStatus(status?: string): string {
  if (status === 'running') return '进行中'
  if (status === 'waiting') return '等待'
  if (status === 'error') return '失败'
  return '完成'
}

function formatCommandText(message: ChatMessage): string {
  const input = parseStepInput(message)
  const command = typeof input.command === 'string' ? input.command.trim() : ''
  return command || message.step?.startedLabel || message.step?.label || message.content
}

function formatShellOutput(message: ChatMessage): string {
  const detail = message.step?.detail?.trim()
  if (!detail) return ''
  return detail
}

function formatElapsedTime(startedAt?: number, finishedAt?: number): string {
  if (!startedAt) return '1s'

  const elapsedSeconds = Math.max(1, Math.round(((finishedAt || Date.now()) - startedAt) / 1000))
  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60

  if (!minutes) return `${seconds}s`
  return `${minutes}m ${seconds}s`
}

function formatCommandSummary(message: ChatMessage): string {
  const status = message.step?.status
  const prefix = status === 'running' || status === 'waiting' ? '正在运行' : '已运行'
  const suffix = status === 'error' ? '，失败' : ''

  return `${prefix} ${formatCommandText(message)}，已持续 ${formatElapsedTime(
    message.startedAt,
    message.finishedAt
  )}${suffix}`
}

function toggleCommandOutput(messageId: number): void {
  const next = new Set(expandedCommandOutputs.value)
  if (next.has(messageId)) {
    next.delete(messageId)
  } else {
    next.add(messageId)
  }
  expandedCommandOutputs.value = next
}

function isCommandOutputCollapsed(messageId: number): boolean {
  return !expandedCommandOutputs.value.has(messageId)
}

function toggleActivitySection(key: string): void {
  const next = new Set(expandedActivitySections.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedActivitySections.value = next
}

// 工具块默认折叠（不在 expandedActivitySections 中 = 折叠）
function isActivitySectionExpanded(key: string): boolean {
  return expandedActivitySections.value.has(key)
}

type MessageScrollSnapshot = {
  length: number
  firstId: number | null
  lastId: number | null
  lastContent: string
  historyLoadRevision: number
}

function getMessageScrollSnapshot(): MessageScrollSnapshot {
  const firstMessage = props.messages[0]
  const lastMessage = props.messages[props.messages.length - 1]
  return {
    length: props.messages.length,
    firstId: firstMessage?.id ?? null,
    lastId: lastMessage?.id ?? null,
    lastContent: lastMessage?.content ?? '',
    historyLoadRevision: chat.historyLoadRevision
  }
}

async function scrollToBottom(behavior: ScrollBehavior = 'smooth'): Promise<void> {
  await nextTick()
  const transcriptElement = transcript.value
  if (!transcriptElement) return

  if (behavior === 'auto') {
    transcriptElement.scrollTop = transcriptElement.scrollHeight
    return
  }

  transcriptElement.scrollTo({
    top: transcriptElement.scrollHeight,
    behavior
  })
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  textarea.remove()
}

async function handleTranscriptClick(event: MouseEvent): Promise<void> {
  const target = event.target
  if (!(target instanceof Element)) return

  const button = target.closest<HTMLButtonElement>('[data-copy-code="true"]')
  if (!button) return

  const codeBlock = button.closest('.code-block')
  const code = codeBlock?.querySelector('pre code')
  const text = code?.textContent || ''
  if (!text) return

  try {
    await copyText(text)
    button.textContent = '已复制'
    button.classList.add('copied')
    window.setTimeout(() => {
      button.textContent = '复制'
      button.classList.remove('copied')
    }, 1400)
  } catch {
    button.textContent = '复制失败'
    window.setTimeout(() => {
      button.textContent = '复制'
    }, 1400)
  }
}

function formatMessageTime(timestamp?: number): string {
  const normalizedTimestamp = timestamp && timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp
  return new Date(normalizedTimestamp || Date.now()).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  })
}

async function copyMessageContent(messageId: number): Promise<void> {
  const message = props.messages.find((m) => m.id === messageId)
  if (!message) return

  try {
    await copyText(message.content)
  } catch {
    // 静默失败
  }
}

function toggleFileGroup(messageId: number): void {
  const next = new Set(expandedFileGroups.value)
  if (next.has(messageId)) {
    next.delete(messageId)
  } else {
    next.add(messageId)
  }
  expandedFileGroups.value = next
}

function isFileGroupCollapsed(messageId: number): boolean {
  return !expandedFileGroups.value.has(messageId)
}

function getUndoableFiles(message: ChatMessage): string[] {
  return (message.changedFiles || [])
    .filter((file) => file.can_undo)
    .map((file) => file.path)
    .filter(Boolean)
}

function undoChangedFiles(message: ChatMessage): void {
  getUndoableFiles(message).forEach((path) => runtime.undoFile(path))
}

watch(
  getMessageScrollSnapshot,
  (current, previous) => {
    const isHistoryLoad = Boolean(
      previous && current.historyLoadRevision !== previous.historyLoadRevision
    )
    const isBulkLoad = previous
      ? Math.abs(current.length - previous.length) > 1 ||
        (current.length > 1 &&
          current.firstId !== previous.firstId &&
          current.lastId !== previous.lastId)
      : current.length > 1

    void scrollToBottom(isHistoryLoad || isBulkLoad ? 'auto' : 'smooth')
  },
  { flush: 'post' }
)
</script>

<template>
  <div ref="transcript" class="transcript" @click="handleTranscriptClick">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message"
      :class="[
        `message-${message.role}`,
        {
          'message-hidden': isMessageHidden(message),
          'message-with-activity-result': isAssistantActivityResult(message)
        }
      ]"
    >
      <template v-if="message.role === 'activity'">
        <div class="activity-shell">
          <div class="activity-heading">
            <button
              class="activity-pill"
              type="button"
              :class="{
                'activity-pill-error': message.content.includes('出错')
              }"
              @click="chat.toggleActivity(message.id)"
            >
              <span>{{ message.content }}</span>
              <ChevronRight :class="{ expanded: !message.collapsed }" />
            </button>
          </div>

          <div
            v-if="getActivityTraceBlocks(message.id).length"
            v-show="!message.collapsed"
            class="activity-trace"
          >
            <template
              v-for="block in getActivityTraceBlocks(message.id)"
              :key="block.type === 'text' ? `text:${block.messageId}` : block.key"
            >
              <!-- 文本块: 渲染为行内 markdown -->
              <!-- eslint-disable vue/no-v-html -->
              <div
                v-if="block.type === 'text'"
                class="activity-trace-text markdown-body"
                v-html="renderMarkdown(block.content)"
              ></div>
              <!-- eslint-enable vue/no-v-html -->

              <!-- 工具块: 可折叠的紧凑摘要 -->
              <section v-else class="activity-trace-section">
                <button
                  class="activity-trace-summary"
                  type="button"
                  @click="toggleActivitySection(block.key)"
                >
                  <span class="activity-trace-icon">
                    <component :is="getActivityIcon(block.kind)" />
                  </span>
                  <span>{{ block.title }}</span>
                  <ChevronRight
                    :class="{ expanded: isActivitySectionExpanded(block.key) }"
                    aria-hidden="true"
                  />
                </button>

                <div v-show="isActivitySectionExpanded(block.key)" class="activity-trace-list">
                  <template v-for="event in block.events" :key="event.id">
                    <template v-if="event.step?.kind === 'command'">
                      <button
                        type="button"
                        class="activity-command-summary"
                        @click="toggleCommandOutput(event.id)"
                      >
                        <span>{{ formatCommandSummary(event) }}</span>
                        <ChevronRight
                          :class="{ expanded: !isCommandOutputCollapsed(event.id) }"
                          aria-hidden="true"
                        />
                      </button>
                      <div
                        v-show="!isCommandOutputCollapsed(event.id)"
                        v-if="formatShellOutput(event)"
                        class="activity-shell-output"
                      >
                        <div class="activity-shell-header">
                          <span>Shell</span>
                          <IconButton
                            label="复制命令输出"
                            size="sm"
                            @click="copyText(formatShellOutput(event))"
                          >
                            <Copy />
                          </IconButton>
                        </div>
                        <pre><code>$ {{ formatCommandText(event) }}
{{ formatShellOutput(event) }}</code></pre>
                        <span
                          class="activity-shell-status"
                          :class="{ error: event.step?.status === 'error' }"
                        >
                          {{ event.step?.status === 'error' ? '失败' : '成功' }}
                        </span>
                      </div>
                    </template>
                    <p
                      v-else
                      class="activity-trace-item"
                      :class="[`activity-trace-item-${event.step?.status || 'success'}`]"
                    >
                      <span>{{ formatActivityStatus(event.step?.status) }}</span>
                      <span>{{ formatActivityTraceLine(event) }}</span>
                    </p>
                  </template>
                </div>
              </section>
            </template>
          </div>
        </div>
      </template>

      <template v-else>
        <div v-if="message.meta && message.role !== 'assistant'" class="message-meta">
          {{ message.meta }}
        </div>
        <!-- eslint-disable vue/no-v-html -->
        <div
          v-if="message.role === 'assistant'"
          class="bubble markdown-body"
          v-html="renderMarkdown(message.content)"
        ></div>
        <!-- eslint-enable vue/no-v-html -->
        <div v-else class="bubble">{{ message.content }}</div>

        <!-- 消息操作栏 -->
        <div
          v-if="message.role === 'user' || message.role === 'assistant'"
          class="message-actions"
          :class="{ 'always-visible': message.role === 'assistant' }"
        >
          <span class="message-time">{{ formatMessageTime(message.timestamp) }}</span>
          <IconButton
            class="action-button"
            label="复制消息"
            size="sm"
            @click="copyMessageContent(message.id)"
          >
            <Copy />
          </IconButton>
        </div>

        <!-- Changed Files with Undo -->
        <div
          v-if="message.role === 'assistant' && message.changedFiles?.length"
          class="changed-files"
        >
          <div class="changed-files-summary">
            <div class="summary-left">
              <PencilLine :size="20" />
              <span class="summary-text">已编辑 {{ message.changedFiles.length }} 个文件</span>
            </div>
            <div class="summary-actions">
              <button
                type="button"
                class="summary-action-button"
                :disabled="!getUndoableFiles(message).length"
                @click="undoChangedFiles(message)"
              >
                撤销
              </button>
            </div>
          </div>
          <div class="changed-files-list">
            <div
              v-for="(file, index) in message.changedFiles"
              v-show="index < 3 || !isFileGroupCollapsed(message.id)"
              :key="file.path"
              class="changed-file-item"
            >
              <span class="file-path">{{ file.path }}</span>
              <span class="file-stats">
                <span class="stat-add">+{{ file.additions || 0 }}</span>
                <span class="stat-del">-{{ file.deletions || 0 }}</span>
              </span>
              <button
                v-if="file.can_undo"
                type="button"
                class="undo-button"
                @click="runtime.undoFile(file.path)"
              >
                撤销
              </button>
            </div>
            <button
              v-if="message.changedFiles.length > 3"
              type="button"
              class="show-more-files"
              @click="toggleFileGroup(message.id)"
            >
              <span>{{
                isFileGroupCollapsed(message.id)
                  ? `再显示 ${message.changedFiles.length - 3} 个文件`
                  : '收起'
              }}</span>
              <ChevronRight :class="{ expanded: !isFileGroupCollapsed(message.id) }" />
            </button>
          </div>
        </div>
      </template>
    </article>
  </div>
</template>

<style scoped>
.changed-files {
  margin-top: 12px;
  padding: 12px;
  background: var(--surface-subtle);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  font-size: 13px;
}

.changed-files-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0;
  background: transparent;
  border: none;
  margin-bottom: 12px;
}

.summary-left {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-primary);
}

.summary-text {
  font-size: 13px;
  font-weight: 500;
}

.summary-actions {
  display: flex;
  gap: 8px;
}

.summary-action-button {
  padding: 4px 12px;
  background: transparent;
  border: 1px solid var(--border-control);
  border-radius: 6px;
  color: var(--text-control);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.summary-action-button:hover {
  background: var(--control-hover-bg);
  border-color: var(--border-hover);
  color: var(--text-control-strong);
}

.summary-action-button:disabled {
  cursor: default;
  opacity: 0.45;
}

.summary-action-button:disabled:hover {
  background: transparent;
  border-color: var(--border-control);
  color: var(--text-control);
}

.changed-files-list {
  margin-top: 0;
}

.changed-file-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(72px, auto) auto;
  align-items: center;
  padding: 8px 0;
  column-gap: 12px;
}

.changed-file-item + .changed-file-item {
  border-top: 1px solid var(--border-muted);
}

.file-stats {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  min-width: 72px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.stat-add {
  color: #22c55e;
}

.stat-del {
  color: #ef4444;
}

.show-more-files {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 0;
  margin-top: 4px;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: color 0.15s ease;
}

.show-more-files:hover {
  color: var(--text-primary);
}

.show-more-files svg {
  width: 14px;
  height: 14px;
  transition: transform 0.2s ease;
}

.show-more-files svg.expanded {
  transform: rotate(90deg);
}

.file-path {
  color: var(--text-primary);
  font-family: monospace;
  font-size: 12px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.undo-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 54px;
  padding: 4px 10px;
  background: var(--control-bg);
  border: 1px solid var(--border-control);
  border-radius: 6px;
  color: var(--text-control);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.undo-button:hover {
  background: var(--control-hover-bg);
  border-color: var(--border-hover);
  color: var(--text-control-strong);
}

.undo-button:active {
  transform: scale(0.96);
}

.message-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  justify-content: flex-end;
  margin-top: 2px;
  padding-right: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.message-user:hover .message-actions {
  opacity: 1;
}

.message-actions.always-visible {
  opacity: 0.6;
}

.message-actions.always-visible:hover {
  opacity: 1;
}

.message-time {
  font-size: 11px;
  color: var(--text-muted);
  opacity: 0.7;
}

.action-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0.6;
  transition: all 0.15s ease;
}

.action-button:hover {
  background: var(--control-hover-bg);
  color: var(--text-control-strong);
  opacity: 1;
}

.action-button:active {
  transform: scale(0.96);
}
</style>
