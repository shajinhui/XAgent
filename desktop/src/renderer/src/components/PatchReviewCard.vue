<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, FileDiff, RotateCcw, X, XCircle } from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
import type {
  PatchLifecycleEvent,
  RuntimePatchChange,
  RuntimePatchMetadata,
  RuntimePatchTestResult
} from '@renderer/types/runtimeEvents'

const props = defineProps<{
  patch: PatchLifecycleEvent
  disabled?: boolean
}>()

const emit = defineEmits<{
  apply: [
    patchId: string,
    selectedPaths: string[],
    testCommand?: string,
    testTimeout?: number
  ]
  reject: [patchId: string]
  dismiss: []
}>()

type DiffLineKind = 'add' | 'delete' | 'meta' | 'context'
type DiffLine = {
  text: string
  kind: DiffLineKind
}

const patchId = computed(() => props.patch.patch_id || '')
const shortPatchId = computed(() => (patchId.value ? patchId.value.slice(0, 8) : 'patch'))
const selectedPathSet = ref<Set<string>>(new Set())
const testCommandInput = ref('')
const testTimeoutInput = ref('')
const changes = computed<RuntimePatchChange[]>(() => {
  if (props.patch.changes?.length) return props.patch.changes
  return (props.patch.changed_paths || []).map((path) => ({
    path,
    change_type: 'update',
    unified_diff: '',
    additions: 0,
    deletions: 0,
    move_path: null
  }))
})
const totalAdditions = computed(() => props.patch.additions ?? sumBy(changes.value, 'additions'))
const totalDeletions = computed(() => props.patch.deletions ?? sumBy(changes.value, 'deletions'))
const selectedPaths = computed(() =>
  changes.value.map((change) => change.path).filter((path) => selectedPathSet.value.has(path))
)
const selectedCount = computed(() => selectedPaths.value.length)
const allSelected = computed(
  () => changes.value.length > 0 && selectedCount.value === changes.value.length
)
const isWaitingApproval = computed(() => props.patch.type === 'patch_approval_request')
const isFailed = computed(() => props.patch.type === 'patch_apply_failed')
const canChoose = computed(
  () => !props.disabled && !isWaitingApproval.value && !['patch_applied', 'patch_rejected'].includes(props.patch.type)
)
const canApply = computed(() => canChoose.value && selectedCount.value > 0)
const metadata = computed<RuntimePatchMetadata>(() => (props.patch.metadata || {}) as RuntimePatchMetadata)
const latestTestResult = computed<RuntimePatchTestResult | null>(() => {
  const direct = objectRecord(metadata.value.test_result)
  if (direct) return direct as RuntimePatchTestResult

  const history = metadata.value.test_results
  if (!Array.isArray(history)) return null
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const item = objectRecord(history[index])
    if (item) return item as RuntimePatchTestResult
  }
  return null
})
const normalizedTestCommand = computed(() => testCommandInput.value.trim())
const normalizedTestTimeout = computed<number | undefined>(() => {
  const raw = testTimeoutInput.value.trim()
  if (!raw) return undefined
  const parsed = Number.parseInt(raw, 10)
  if (!Number.isFinite(parsed) || parsed < 1) return undefined
  return Math.min(parsed, 600)
})
const testStatusLabel = computed(() => {
  const result = latestTestResult.value
  if (!result) return ''
  if (result.blocked) return '测试被安全策略拦截'
  return result.ok ? '测试通过' : '测试失败'
})
const testStatusClass = computed(() => {
  const result = latestTestResult.value
  if (!result) return 'is-neutral'
  if (result.blocked) return 'is-blocked'
  return result.ok ? 'is-pass' : 'is-fail'
})
const testMetaLabel = computed(() => {
  const result = latestTestResult.value
  if (!result) return ''

  const parts: string[] = []
  if (typeof result.exit_code === 'number') parts.push(`exit ${result.exit_code}`)
  const source = formatTestSource(result.source)
  if (source) parts.push(source)
  const duration = formatDuration(result.duration_seconds)
  if (duration) parts.push(duration)
  return parts.join(' · ')
})
const latestTestOutput = computed(() => {
  const result = latestTestResult.value
  if (!result) return ''
  const output = String(result.output || '').trim()
  return result.ok && !result.blocked ? '' : output
})
const failureStageLabel = computed(() => formatFailureStage(metadata.value.failure_stage))
const failureWrittenPaths = computed(() => stringArray(metadata.value.written_paths))
const failureRemainingPaths = computed(() => stringArray(metadata.value.remaining_paths))
const failureFailedPath = computed(() => String(metadata.value.failed_path || '').trim())
const failureDirtyHint = computed(
  () => metadata.value.partially_written === true && !!failureFailedPath.value
)
const statusLabel = computed(() => {
  if (props.patch.type === 'patch_applied') return '已应用'
  if (props.patch.type === 'patch_rejected') return '已拒绝'
  if (props.patch.type === 'patch_apply_failed') return '应用失败'
  if (props.patch.type === 'patch_approval_request') return '等待权限确认'
  return '等待审查'
})
const primaryLabel = computed(() => {
  if (isFailed.value) return selectedCount.value === changes.value.length ? '重试全部' : `重试所选 (${selectedCount.value})`
  return allSelected.value ? '应用全部' : `应用所选 (${selectedCount.value})`
})

watch(
  () => `${patchId.value}:${changes.value.map((change) => change.path).join('\u0000')}`,
  () => selectAll(),
  { immediate: true }
)

watch(
  () => patchId.value,
  () => {
    testCommandInput.value = ''
    testTimeoutInput.value = ''
  },
  { immediate: true }
)

function sumBy(changes: RuntimePatchChange[], key: 'additions' | 'deletions'): number {
  return changes.reduce((total, change) => total + (Number(change[key]) || 0), 0)
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function diffLines(change: RuntimePatchChange): DiffLine[] {
  const diff = change.unified_diff || ''
  if (!diff.trim()) {
    return [{ text: '暂无可展示 diff。', kind: 'context' }]
  }
  return diff.split(/\r?\n/).map((line) => ({
    text: line || ' ',
    kind: diffLineKind(line)
  }))
}

function diffLineKind(line: string): DiffLineKind {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'delete'
  return 'context'
}

function changeTypeLabel(changeType: string): string {
  if (changeType === 'add') return '新增'
  if (changeType === 'delete') return '删除'
  return '修改'
}

function formatTestSource(source: string | undefined): string {
  if (source === 'explicit') return '显式命令'
  if (source === 'project_config') return '项目默认命令'
  if (source === 'agents_md') return 'AGENTS.md 默认命令'
  return String(source || '').trim()
}

function formatDuration(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return ''
  if (value < 10) return `${value.toFixed(1)}s`
  return `${Math.round(value)}s`
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter((item) => item.length > 0)
}

function formatFailureStage(stage: string | undefined): string {
  if (stage === 'validate') return '校验'
  if (stage === 'rollback_validate') return '回滚校验'
  if (stage === 'preflight') return 'Git 预检'
  if (stage === 'write') return '写入'
  return String(stage || '').trim()
}

function isSelected(path: string): boolean {
  return selectedPathSet.value.has(path)
}

function toggleSelected(path: string): void {
  const next = new Set(selectedPathSet.value)
  if (next.has(path)) {
    next.delete(path)
  } else {
    next.add(path)
  }
  selectedPathSet.value = next
}

function selectAll(): void {
  selectedPathSet.value = new Set(changes.value.map((change) => change.path))
}

function clearSelection(): void {
  selectedPathSet.value = new Set()
}

function applySelected(): void {
  if (!canApply.value) return
  emit(
    'apply',
    patchId.value,
    selectedPaths.value,
    normalizedTestCommand.value || undefined,
    normalizedTestTimeout.value
  )
}
</script>

<template>
  <section class="patch-review-card" role="group" aria-labelledby="patch-review-title">
    <header class="patch-review-heading">
      <div class="patch-title-block">
        <span class="patch-icon"><FileDiff /></span>
        <div>
          <h2 id="patch-review-title">Patch 待审查</h2>
          <p>
            {{ statusLabel }} · {{ changes.length }} 个文件 ·
            <span class="stat-add">+{{ totalAdditions }}</span>
            /
            <span class="stat-del">-{{ totalDeletions }}</span>
          </p>
        </div>
      </div>
      <IconButton class="patch-icon-action" label="收起 Patch 审查卡片" @click="emit('dismiss')">
        <X />
      </IconButton>
    </header>

    <div class="patch-meta-row">
      <span class="patch-id">patch_id: {{ shortPatchId }}</span>
      <span v-if="patch.summary" class="patch-summary">{{ patch.summary }}</span>
    </div>

    <div class="patch-selection-row">
      <span>已选择 {{ selectedCount }} / {{ changes.length }} 个文件</span>
      <div class="patch-selection-actions">
        <button type="button" :disabled="!canChoose || allSelected" @click="selectAll">全选</button>
        <button type="button" :disabled="!canChoose || selectedCount === 0" @click="clearSelection">清空</button>
      </div>
    </div>

    <section v-if="canChoose" class="patch-test-config">
      <label class="patch-test-field patch-test-command">
        <span>应用后测试命令（可选）</span>
        <input
          v-model="testCommandInput"
          type="text"
          :disabled="disabled"
          placeholder="留空则仅使用项目默认测试命令（如果已配置）"
        />
      </label>
      <label class="patch-test-field patch-test-timeout">
        <span>超时（秒）</span>
        <input
          v-model="testTimeoutInput"
          type="number"
          min="1"
          max="600"
          inputmode="numeric"
          :disabled="disabled"
          placeholder="60"
        />
      </label>
    </section>

    <section v-if="latestTestResult" class="patch-test-result" :class="testStatusClass">
      <header class="patch-test-result-heading">
        <span class="patch-test-badge" :class="testStatusClass">{{ testStatusLabel }}</span>
        <span v-if="testMetaLabel" class="patch-test-meta">{{ testMetaLabel }}</span>
      </header>
      <p v-if="latestTestResult.command" class="patch-test-command-line">
        {{ latestTestResult.command }}
      </p>
      <pre v-if="latestTestOutput" class="patch-test-output">{{ latestTestOutput }}</pre>
    </section>

    <section v-if="isFailed" class="patch-failure-summary">
      <p v-if="failureStageLabel" class="patch-failure-line">
        <span class="patch-failure-label">失败阶段</span>
        <span>{{ failureStageLabel }}</span>
      </p>
      <div v-if="failureWrittenPaths.length" class="patch-failure-block">
        <p class="patch-failure-label">已写入</p>
        <ul>
          <li v-for="path in failureWrittenPaths" :key="'written:' + path">{{ path }}</li>
        </ul>
      </div>
      <div v-if="failureFailedPath" class="patch-failure-block">
        <p class="patch-failure-label">失败文件</p>
        <ul>
          <li>{{ failureFailedPath }}</li>
        </ul>
        <p v-if="failureDirtyHint" class="patch-failure-hint">这个文件可能已经部分写入，建议优先检查。</p>
      </div>
      <div v-if="failureRemainingPaths.length" class="patch-failure-block">
        <p class="patch-failure-label">未触及</p>
        <ul>
          <li v-for="path in failureRemainingPaths" :key="'remaining:' + path">{{ path }}</li>
        </ul>
      </div>
    </section>

    <div class="patch-file-list">
      <section v-for="change in changes" :key="change.path" class="patch-file">
        <header class="patch-file-heading">
          <label class="patch-file-selector">
            <input
              type="checkbox"
              :checked="isSelected(change.path)"
              :disabled="!canChoose"
              @change="toggleSelected(change.path)"
            />
            <span>
              <span class="change-type">{{ changeTypeLabel(change.change_type) }}</span>
              <span class="patch-path">{{ change.path }}</span>
            </span>
          </label>
          <span class="patch-file-stats">
            <span class="stat-add">+{{ change.additions || 0 }}</span>
            <span class="stat-del">-{{ change.deletions || 0 }}</span>
          </span>
        </header>

        <pre class="patch-diff" aria-label="Unified diff"><code><span
          v-for="(line, index) in diffLines(change)"
          :key="change.path + ':' + index"
          class="diff-line"
          :class="'diff-line-' + line.kind"
        >{{ line.text }}</span></code></pre>
      </section>
    </div>

    <footer class="patch-review-actions">
      <button
        class="patch-secondary-action"
        type="button"
        :disabled="!canChoose"
        @click="emit('reject', patchId)"
      >
        <XCircle />
        <span>拒绝</span>
      </button>
      <button
        class="patch-primary-action"
        type="button"
        :disabled="!canApply"
        @click="applySelected"
      >
        <component :is="isFailed ? RotateCcw : Check" />
        <span>{{ primaryLabel }}</span>
      </button>
    </footer>
  </section>
</template>

<style scoped>
.patch-review-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--border-soft);
  border-radius: 14px;
  background: var(--panel-elevated-bg);
  box-shadow: 0 18px 50px var(--shadow-panel);
}

.patch-review-heading,
.patch-title-block,
.patch-file-heading,
.patch-review-actions,
.patch-meta-row,
.patch-selection-row,
.patch-selection-actions,
.patch-file-selector {
  display: flex;
  align-items: center;
}

.patch-review-heading {
  justify-content: space-between;
  gap: 12px;
}

.patch-title-block {
  min-width: 0;
  gap: 10px;
}

.patch-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--surface-subtle);
  color: var(--accent);
}

.patch-icon svg,
.patch-review-actions svg {
  width: 16px;
  height: 16px;
}

.patch-review-heading h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 650;
}

.patch-review-heading p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-meta-row {
  flex-wrap: wrap;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-selection-row {
  justify-content: space-between;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-test-config {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 108px;
  gap: 8px;
}

.patch-test-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-test-field input {
  min-height: 34px;
  padding: 0 10px;
  border: 1px solid var(--border-control);
  border-radius: 10px;
  background: var(--field-bg);
  color: var(--text-primary);
  font: inherit;
}

.patch-test-field input:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.patch-test-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border-muted);
  border-radius: 10px;
  background: var(--surface-subtle);
}

.patch-test-result.is-pass {
  border-color: rgba(34, 197, 94, 0.35);
}

.patch-test-result.is-fail {
  border-color: rgba(239, 68, 68, 0.35);
}

.patch-test-result.is-blocked {
  border-color: rgba(245, 158, 11, 0.45);
}

.patch-test-result-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.patch-test-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 650;
}

.patch-test-badge.is-pass {
  background: rgba(34, 197, 94, 0.14);
  color: #16a34a;
}

.patch-test-badge.is-fail {
  background: rgba(239, 68, 68, 0.14);
  color: #dc2626;
}

.patch-test-badge.is-blocked {
  background: rgba(245, 158, 11, 0.16);
  color: #d97706;
}

.patch-test-badge.is-neutral {
  background: var(--surface-subtle);
  color: var(--text-secondary);
}

.patch-test-meta {
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-test-command-line,
.patch-test-output {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.patch-test-command-line {
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.patch-test-output {
  max-height: 160px;
  overflow: auto;
  padding: 10px;
  border-radius: 10px;
  background: var(--code-block-bg);
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.patch-failure-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(239, 68, 68, 0.24);
  border-radius: 10px;
  background: rgba(239, 68, 68, 0.05);
}

.patch-failure-line,
.patch-failure-block > p,
.patch-failure-block ul,
.patch-failure-hint {
  margin: 0;
}

.patch-failure-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.patch-failure-label {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.patch-failure-block ul {
  padding-left: 18px;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.patch-failure-hint {
  color: #b45309;
  font-size: 12px;
}

.patch-selection-actions {
  gap: 6px;
}

.patch-selection-actions button {
  padding: 4px 8px;
  border: 1px solid var(--border-muted);
  border-radius: 999px;
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.patch-selection-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.patch-id {
  padding: 3px 7px;
  border: 1px solid var(--border-muted);
  border-radius: 999px;
  font-family: var(--font-mono);
}

.patch-summary {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.patch-file-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: min(46vh, 520px);
  overflow: auto;
  padding-right: 2px;
}

.patch-file {
  overflow: hidden;
  border: 1px solid var(--border-muted);
  border-radius: 10px;
  background: var(--surface-subtle);
}

.patch-file-heading {
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-muted);
  font-size: 12px;
}

.patch-file-selector {
  min-width: 0;
  gap: 8px;
}

.patch-file-selector input {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  accent-color: var(--accent);
}

.patch-file-selector > span {
  min-width: 0;
}

.change-type {
  margin-right: 8px;
  color: var(--text-secondary);
}

.patch-path {
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.patch-file-stats {
  display: inline-flex;
  gap: 8px;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.stat-add {
  color: #22c55e;
}

.stat-del {
  color: #ef4444;
}

.patch-diff {
  max-height: 280px;
  margin: 0;
  overflow: auto;
  background: var(--code-block-bg);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.patch-diff code {
  display: block;
  padding: 8px 0;
}

.diff-line {
  display: block;
  min-width: max-content;
  padding: 0 10px;
  white-space: pre;
}

.diff-line-add {
  background: rgba(34, 197, 94, 0.12);
  color: #16a34a;
}

.diff-line-delete {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}

.diff-line-meta {
  color: var(--text-secondary);
}

.patch-review-actions {
  justify-content: flex-end;
  gap: 8px;
}

.patch-primary-action,
.patch-secondary-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 13px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}

.patch-primary-action {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: var(--text-on-primary);
}

.patch-secondary-action {
  border: 1px solid var(--border-control);
  background: transparent;
  color: var(--text-control);
}

.patch-primary-action:disabled,
.patch-secondary-action:disabled {
  cursor: default;
  opacity: 0.5;
}

@media (max-width: 720px) {
  .patch-test-config {
    grid-template-columns: 1fr;
  }
}
</style>
