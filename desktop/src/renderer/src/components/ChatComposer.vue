<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ChevronDown, Paperclip, SendHorizontal, Shield } from 'lucide-vue-next'
import IconButton from '@renderer/components/ui/IconButton.vue'
import type { RuntimePermissionMode } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
  model: string
  modelOptions: string[]
  reasoningEffort: string
  reasoningOptions: string[]
  permissionMode: RuntimePermissionMode
}>()

const emit = defineEmits<{
  send: [content: string]
  'update:model': [model: string]
  'update:reasoningEffort': [effort: string]
  'update:permissionMode': [mode: RuntimePermissionMode]
}>()

const draft = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const isSubmitting = ref(false)
const permissionMenuOpen = ref(false)
let submitUnlockTimer: number | null = null
const reasoningLabels: Record<string, string> = {
  off: '思考 关',
  low: '思考 低',
  medium: '思考 中',
  high: '思考 高'
}
const permissionModes: Array<{
  value: RuntimePermissionMode
  label: string
  description: string
}> = [
  {
    value: 'request_approval',
    label: '请求批准',
    description: '写入文件和风险命令先询问'
  },
  {
    value: 'auto_approve',
    label: '替我审批',
    description: '常规工作区操作自动继续'
  },
  {
    value: 'full_access',
    label: '完全访问',
    description: '不套沙箱，允许访问本机文件和网络'
  },
  {
    value: 'custom',
    label: '自定义',
    description: '使用 trusted config.toml 中的权限'
  }
]
const activePermissionMode = computed(
  () => permissionModes.find((mode) => mode.value === props.permissionMode) || permissionModes[0]
)

function formatModelLabel(model: string): string {
  const [provider, ...modelParts] = model.split('/')
  const modelName = modelParts.join('/')
  return modelName ? `${provider} · ${modelName}` : model
}

function formatReasoningLabel(effort: string): string {
  return reasoningLabels[effort] || effort
}

function togglePermissionMenu(): void {
  if (props.disabled) return
  permissionMenuOpen.value = !permissionMenuOpen.value
}

function selectPermissionMode(mode: RuntimePermissionMode): void {
  permissionMenuOpen.value = false
  if (mode === props.permissionMode) return
  emit('update:permissionMode', mode)
}

function clearDraft(): void {
  draft.value = ''
  if (textareaRef.value) {
    textareaRef.value.value = ''
  }
}

function unlockSubmitSoon(): void {
  if (submitUnlockTimer) {
    window.clearTimeout(submitUnlockTimer)
  }

  submitUnlockTimer = window.setTimeout(() => {
    isSubmitting.value = false
    submitUnlockTimer = null
  }, 250)
}

function sendMessage(): void {
  if (props.disabled || isSubmitting.value) return

  const text = draft.value.trim()
  if (!text) {
    clearDraft()
    return
  }

  isSubmitting.value = true
  clearDraft()
  emit('send', text)
  unlockSubmitSoon()
}

function handleEnter(event: KeyboardEvent): void {
  if (event.isComposing) return

  event.preventDefault()
  sendMessage()
}

onBeforeUnmount(() => {
  if (submitUnlockTimer) {
    window.clearTimeout(submitUnlockTimer)
  }
})
</script>

<template>
  <form class="composer" @submit.prevent="sendMessage">
    <textarea
      ref="textareaRef"
      v-model="draft"
      :placeholder="placeholder || '输入消息...'"
      :disabled="disabled || isSubmitting"
      rows="1"
      @keydown.enter.exact="handleEnter"
    ></textarea>

    <div class="composer-actions">
      <div class="left-tools">
        <IconButton label="添加附件" variant="soft">
          <Paperclip />
        </IconButton>
        <div class="permission-mode-control">
          <button
            class="permission-mode-trigger"
            :class="{ danger: permissionMode === 'full_access' }"
            type="button"
            aria-label="切换权限模式"
            :aria-expanded="permissionMenuOpen"
            :disabled="disabled"
            @click="togglePermissionMenu"
          >
            <Shield />
            <span>{{ activePermissionMode.label }}</span>
            <ChevronDown class="chevron" />
          </button>
          <div v-if="permissionMenuOpen" class="permission-mode-menu">
            <button
              v-for="mode in permissionModes"
              :key="mode.value"
              class="permission-mode-option"
              :class="{ selected: mode.value === permissionMode }"
              type="button"
              @click="selectPermissionMode(mode.value)"
            >
              <span>{{ mode.label }}</span>
              <small>{{ mode.description }}</small>
            </button>
          </div>
        </div>
      </div>

      <div class="runtime-controls" aria-label="模型配置">
        <label class="composer-select-wrap">
          <span class="sr-only">选择模型</span>
          <select
            class="composer-select model-select"
            :value="model"
            :disabled="disabled"
            aria-label="选择模型"
            @change="emit('update:model', ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="option in modelOptions" :key="option" :value="option">
              {{ formatModelLabel(option) }}
            </option>
          </select>
        </label>
        <label class="composer-select-wrap">
          <span class="sr-only">选择思考程度</span>
          <select
            class="composer-select reasoning-select"
            :value="reasoningEffort"
            :disabled="disabled"
            aria-label="选择思考程度"
            @change="emit('update:reasoningEffort', ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="option in reasoningOptions" :key="option" :value="option">
              {{ formatReasoningLabel(option) }}
            </option>
          </select>
        </label>
      </div>

      <button
        class="send-button"
        type="submit"
        aria-label="发送消息"
        :disabled="disabled || isSubmitting"
      >
        <SendHorizontal />
      </button>
    </div>
  </form>
</template>
