<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import type { PermissionRequestEvent } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  request: PermissionRequestEvent
}>()

const emit = defineEmits<{
  approve: [scope?: 'once' | 'session']
  deny: [feedback?: string]
}>()

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') {
    event.preventDefault()
    emit('approve', 'once')
  } else if (event.key === 'Escape') {
    event.preventDefault()
    emit('deny')
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})

const commandPreview = computed(() => {
  const command = metadataText('command') || commandFromArguments()
  if (command) return command

  try {
    return JSON.stringify(JSON.parse(props.request.arguments), null, 2)
  } catch {
    return props.request.arguments
  }
})

const question = computed(() => {
  if (props.request.tool === 'run_command') return '允许执行这条命令吗？'
  return `允许执行 ${props.request.tool} 吗？`
})

const reason = computed(() => {
  return metadataText('reason') || props.request.detail || '当前权限模式要求先确认这次操作。'
})

const suggestedPrefix = computed(() => metadataList('suggested_prefix_rule'))
const canApproveSession = computed(
  () => props.request.tool === 'run_command' && suggestedPrefix.value.length > 0
)
const policyItems = computed(() => {
  const cwd = metadataText('cwd') || shortenPath(metadataText('current_dir'))
  const profile = metadataText('permission_profile')
  const network = metadataText('network_policy')
  const sandboxEnabled = metadataBool('sandbox_enabled')
  const items: Array<{ label: string; value: string; tone?: 'danger' | 'warning' }> = []

  if (cwd) items.push({ label: 'cwd', value: cwd })
  if (profile) items.push({ label: '模式', value: formatPermissionProfile(profile) })
  if (network) items.push({ label: '网络', value: formatNetworkPolicy(network) })
  if (sandboxEnabled !== null) {
    items.push({
      label: '沙箱',
      value: sandboxEnabled ? '开启' : '关闭',
      tone: sandboxEnabled ? undefined : 'danger'
    })
  }

  return items
})
const riskNote = computed(() => {
  if (
    metadataBool('sandbox_enabled') === false ||
    metadataText('permission_profile') === 'danger_no_sandbox'
  ) {
    return '完全访问会绕过 macOS Seatbelt，并允许更大范围的本机文件和网络访问。'
  }
  if (metadataBool('network_enabled') === true || metadataText('network_policy') === 'enabled') {
    return '这次操作会在当前工作区边界内运行，但网络访问已开启。'
  }
  return '这次操作会沿用当前工作区边界，网络保持受限。'
})

function metadataText(key: string): string {
  const value = props.request.metadata[key]
  return typeof value === 'string' ? value.trim() : ''
}

function metadataBool(key: string): boolean | null {
  const value = props.request.metadata[key]
  return typeof value === 'boolean' ? value : null
}

function metadataList(key: string): string[] {
  const value = props.request.metadata[key]
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function commandFromArguments(): string {
  try {
    const value = JSON.parse(props.request.arguments)
    return typeof value.command === 'string' ? value.command.trim() : ''
  } catch {
    return ''
  }
}

function shortenPath(path: string): string {
  if (!path) return ''
  const parts = path.split('/').filter(Boolean)
  if (parts.length <= 3) return path
  return `.../${parts.slice(-3).join('/')}`
}

function formatPermissionProfile(value: string): string {
  if (value === 'danger_no_sandbox') return '完全访问'
  if (value === 'read_only') return '只读'
  return '工作区'
}

function formatNetworkPolicy(value: string): string {
  return value === 'enabled' ? '允许' : '受限'
}
</script>

<template>
  <section class="permission-composer" role="group" aria-labelledby="permission-title">
    <header class="permission-heading">
      <p id="permission-title">{{ question }}</p>
      <span>{{ reason }}</span>
    </header>

    <pre v-if="commandPreview" class="permission-command">{{ commandPreview }}</pre>

    <div v-if="policyItems.length" class="permission-policy-summary">
      <span
        v-for="item in policyItems"
        :key="item.label"
        class="permission-policy-chip"
        :class="item.tone"
      >
        {{ item.label }}: {{ item.value }}
      </span>
    </div>

    <p
      class="permission-risk-note"
      :class="{ danger: metadataBool('sandbox_enabled') === false }"
    >
      {{ riskNote }}
    </p>

    <p v-if="suggestedPrefix.length" class="permission-suggestion">
      本会话可记住同类命令：<code>{{ suggestedPrefix.join(' ') }}</code>
    </p>

    <div
      class="permission-options compact"
      :class="{ triple: canApproveSession }"
      aria-label="权限选择"
    >
      <button class="permission-option" type="button" @click="emit('approve', 'once')">
        <strong>本次允许</strong>
      </button>
      <button
        v-if="canApproveSession"
        class="permission-option"
        type="button"
        @click="emit('approve', 'session')"
      >
        <strong>本会话允许</strong>
      </button>
      <button class="permission-option" type="button" @click="emit('deny')">
        <strong>拒绝</strong>
      </button>
    </div>
  </section>
</template>
