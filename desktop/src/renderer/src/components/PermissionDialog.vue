<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PermissionRequestEvent } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  request: PermissionRequestEvent
}>()

const emit = defineEmits<{
  approve: [scope?: 'once' | 'session']
  deny: [feedback?: string]
}>()

const showDenyFeedback = ref(false)
const feedback = ref('')

const formattedArguments = computed(() => {
  try {
    return JSON.stringify(JSON.parse(props.request.arguments), null, 2)
  } catch {
    return props.request.arguments
  }
})

const question = computed(() => `是否允许执行 ${props.request.tool}？`)
const suggestedPrefixRule = computed(() => {
  const value = props.request.metadata.suggested_prefix_rule
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
})
const suggestedPrefixLabel = computed(() => suggestedPrefixRule.value.join(' '))

const permissionFacts = computed(() =>
  [
    fact('目录', metadataText('cwd') || relativeCurrentDir.value),
    fact('Profile', profileLabel(metadataText('permission_profile'))),
    fact('审批', approvalLabel(metadataText('approval_policy'))),
    fact('网络', networkLabel(metadataText('network_policy'))),
    fact('类别', metadataText('category')),
    fact('命令', metadataText('command'))
  ].filter((item): item is { label: string; value: string } => Boolean(item?.value))
)

const relativeCurrentDir = computed(() => {
  const current = metadataText('current_dir')
  const selected = metadataText('selected_root')
  if (!current) return ''
  if (!selected) return current

  const root = trimTrailingSeparators(selected)
  const path = trimTrailingSeparators(current)
  if (path === root) return '.'
  if (path.startsWith(`${root}/`)) return path.slice(root.length + 1)
  return path
})

function submitFeedback(): void {
  emit('deny', feedback.value.trim() || undefined)
}

function fact(label: string, value: string): { label: string; value: string } | null {
  return value ? { label, value } : null
}

function metadataText(key: string): string {
  const value = props.request.metadata[key]
  return typeof value === 'string' ? value.trim() : ''
}

function profileLabel(value: string): string {
  if (value === 'read_only') return '只读'
  if (value === 'workspace_write') return '工作区可写'
  if (value === 'danger_no_sandbox') return '无沙箱'
  return value
}

function approvalLabel(value: string): string {
  if (value === 'ask-before-mutating') return '变更前确认'
  if (value === 'never') return '禁止询问'
  return value
}

function networkLabel(value: string): string {
  if (value === 'restricted') return '受限'
  if (value === 'enabled') return '开启'
  return value
}

function trimTrailingSeparators(path: string): string {
  return path.replace(/[\\/]+$/, '')
}
</script>

<template>
  <section class="permission-composer" role="group" aria-labelledby="permission-title">
    <header class="permission-heading">
      <p id="permission-title">{{ question }}</p>
      <span>{{ request.detail }}</span>
    </header>

    <pre v-if="formattedArguments" class="permission-command">{{ formattedArguments }}</pre>

    <dl v-if="permissionFacts.length" class="permission-facts">
      <div v-for="item in permissionFacts" :key="item.label">
        <dt>{{ item.label }}</dt>
        <dd>{{ item.value }}</dd>
      </div>
    </dl>

    <div v-if="!showDenyFeedback" class="permission-options" aria-label="权限选择">
      <button class="permission-option" type="button" @click="emit('approve', 'once')">
        <span>1.</span>
        <strong>是，允许本次操作</strong>
      </button>
      <button
        v-if="suggestedPrefixRule.length"
        class="permission-option"
        type="button"
        @click="emit('approve', 'session')"
      >
        <span>2.</span>
        <strong>本会话允许 {{ suggestedPrefixLabel }}</strong>
      </button>
      <button class="permission-option" type="button" @click="showDenyFeedback = true">
        <span>{{ suggestedPrefixRule.length ? '3.' : '2.' }}</span>
        <strong>否，告诉 Agent 如何调整</strong>
      </button>
    </div>

    <div v-else class="permission-feedback">
      <textarea
        v-model="feedback"
        rows="3"
        placeholder="可选：告诉 Agent 如何调整方案，比如不要联网、改用本地文件、换一个命令..."
        @keydown.enter.meta.prevent="submitFeedback"
        @keydown.enter.ctrl.prevent="submitFeedback"
      ></textarea>

      <footer class="permission-actions">
        <button class="permission-skip" type="button" @click="emit('deny')">跳过</button>
        <button class="permission-back" type="button" @click="showDenyFeedback = false">
          返回
        </button>
        <button class="permission-submit" type="button" @click="submitFeedback">提交</button>
      </footer>
    </div>
  </section>
</template>
