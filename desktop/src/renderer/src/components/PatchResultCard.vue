<script setup lang="ts">
import { computed } from 'vue'
import { CheckCircle2, RotateCcw, X } from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
import type {
  PatchLifecycleEvent,
  RuntimePatchMetadata,
  RuntimePatchTestResult
} from '@renderer/types/runtimeEvents'

const props = defineProps<{
  patch: PatchLifecycleEvent
}>()

const emit = defineEmits<{
  dismiss: []
}>()

const shortPatchId = computed(() => String(props.patch.patch_id || '').trim().slice(0, 8) || 'patch')
const metadata = computed<RuntimePatchMetadata>(() => (props.patch.metadata || {}) as RuntimePatchMetadata)
const testResults = computed<RuntimePatchTestResult[]>(() => {
  const history = Array.isArray(metadata.value.test_results) ? metadata.value.test_results : []
  if (history.length > 0) return history.filter(Boolean)
  return metadata.value.test_result ? [metadata.value.test_result] : []
})
const latestResult = computed(() => (testResults.value.length ? testResults.value[testResults.value.length - 1] : null))
const title = computed(() => {
  if (props.patch.type === 'patch_rolled_back') return 'Patch 已回滚'
  return 'Patch 测试结果'
})
const statusLabel = computed(() => {
  if (props.patch.type === 'patch_rolled_back') return '已回滚'
  return '已应用'
})
const statusClass = computed(() => {
  const result = latestResult.value
  if (!result) return 'is-neutral'
  if (result.blocked) return 'is-blocked'
  return result.ok ? 'is-pass' : 'is-fail'
})

function formatSource(source: string | undefined): string {
  if (source === 'explicit') return '显式测试命令'
  if (source === 'project_config') return '项目默认测试命令'
  if (source === 'agents_md') return 'AGENTS.md 默认测试命令'
  return String(source || '').trim()
}

function formatHeadline(result: RuntimePatchTestResult): string {
  const status = result.blocked ? '测试被安全策略拦截' : result.ok ? '测试通过' : '测试失败'
  const parts: string[] = []
  if (typeof result.exit_code === 'number') parts.push(`exit ${result.exit_code}`)
  const source = formatSource(result.source)
  if (source) parts.push(source)
  if (typeof result.duration_seconds === 'number' && Number.isFinite(result.duration_seconds)) {
    const seconds = result.duration_seconds
    parts.push(seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`)
  }
  return parts.length ? `${status} · ${parts.join(' · ')}` : status
}

function outputFor(result: RuntimePatchTestResult): string {
  const output = String(result.output || '').trim()
  if (!output || (result.ok && !result.blocked)) return ''
  return output
}
</script>

<template>
  <section class="patch-result-card" :class="statusClass">
    <header class="patch-result-heading">
      <div class="patch-result-title">
        <span class="patch-result-icon">
          <component :is="patch.type === 'patch_rolled_back' ? RotateCcw : CheckCircle2" />
        </span>
        <div>
          <h2>{{ title }}</h2>
          <p>{{ statusLabel }} · patch_id: {{ shortPatchId }} · {{ patch.changed_paths.length }} 个文件</p>
        </div>
      </div>
      <IconButton class="patch-result-dismiss" label="关闭 Patch 结果卡片" @click="emit('dismiss')">
        <X />
      </IconButton>
    </header>

    <p v-if="patch.summary" class="patch-result-summary">{{ patch.summary }}</p>

    <div class="patch-result-files">
      <span v-for="path in patch.changed_paths" :key="path" class="patch-result-file">{{ path }}</span>
    </div>

    <section v-if="testResults.length" class="patch-test-history">
      <header class="patch-test-history-heading">
        <h3>测试历史</h3>
        <span>{{ testResults.length }} 次</span>
      </header>

      <article
        v-for="(result, index) in testResults.slice().reverse()"
        :key="`${result.command || 'test'}:${index}`"
        class="patch-test-history-item"
      >
        <p class="patch-test-history-headline">{{ formatHeadline(result) }}</p>
        <p v-if="result.command" class="patch-test-history-command">{{ result.command }}</p>
        <pre v-if="outputFor(result)" class="patch-test-history-output">{{ outputFor(result) }}</pre>
      </article>
    </section>
  </section>
</template>

<style scoped>
.patch-result-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--border-soft);
  border-radius: 14px;
  background: var(--panel-elevated-bg);
  box-shadow: 0 18px 50px var(--shadow-panel);
}

.patch-result-card.is-pass {
  border-color: rgba(34, 197, 94, 0.32);
}

.patch-result-card.is-fail {
  border-color: rgba(239, 68, 68, 0.32);
}

.patch-result-card.is-blocked {
  border-color: rgba(245, 158, 11, 0.42);
}

.patch-result-heading,
.patch-result-title,
.patch-test-history-heading {
  display: flex;
  align-items: center;
}

.patch-result-heading {
  justify-content: space-between;
  gap: 12px;
}

.patch-result-title {
  gap: 10px;
  min-width: 0;
}

.patch-result-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--surface-subtle);
  color: var(--accent);
}

.patch-result-icon svg {
  width: 16px;
  height: 16px;
}

.patch-result-title h2,
.patch-test-history-heading h3 {
  margin: 0;
  color: var(--text-primary);
}

.patch-result-title h2 {
  font-size: 14px;
  font-weight: 650;
}

.patch-result-title p,
.patch-result-summary,
.patch-test-history-heading span {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-result-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.patch-result-file {
  padding: 4px 8px;
  border-radius: 999px;
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-size: 12px;
}

.patch-test-history {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.patch-test-history-heading {
  justify-content: space-between;
  gap: 12px;
}

.patch-test-history-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--border-muted);
  border-radius: 10px;
  background: var(--surface-subtle);
}

.patch-test-history-headline,
.patch-test-history-command,
.patch-test-history-output {
  margin: 0;
  font-size: 12px;
}

.patch-test-history-headline {
  color: var(--text-primary);
  font-weight: 600;
}

.patch-test-history-command,
.patch-test-history-output {
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-word;
}

.patch-test-history-command {
  color: var(--text-secondary);
}

.patch-test-history-output {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--panel-bg);
  color: var(--text-primary);
  max-height: 200px;
  overflow: auto;
}
</style>
