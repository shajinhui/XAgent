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

function metadataText(key: string): string {
  const value = props.request.metadata[key]
  return typeof value === 'string' ? value.trim() : ''
}

function commandFromArguments(): string {
  try {
    const value = JSON.parse(props.request.arguments)
    return typeof value.command === 'string' ? value.command.trim() : ''
  } catch {
    return ''
  }
}
</script>

<template>
  <section class="permission-composer" role="group" aria-labelledby="permission-title">
    <header class="permission-heading">
      <p id="permission-title">{{ question }}</p>
      <span>Agent 需要你的确认后才会继续。</span>
    </header>

    <pre v-if="commandPreview" class="permission-command">{{ commandPreview }}</pre>

    <div class="permission-options compact" aria-label="权限选择">
      <button class="permission-option" type="button" @click="emit('approve', 'once')">
        <strong>允许 (Enter)</strong>
      </button>
      <button class="permission-option" type="button" @click="emit('deny')">
        <strong>拒绝 (Esc)</strong>
      </button>
    </div>
  </section>
</template>
