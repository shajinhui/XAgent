<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue'
import {
  Blocks,
  Bot,
  Check,
  ChevronDown,
  Code2,
  FileText,
  GitBranch,
  Globe,
  ListChecks,
  Plus,
  Presentation,
  SendHorizontal,
  Shield,
  Table2,
  Wrench,
  X
} from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
import type {
  RuntimePermissionMode,
  RuntimeSkillLoadError,
  RuntimeSkillMetadata
} from '@renderer/types/runtimeEvents'

type SkillIconCarrier = {
  name: string
  description?: string
  short_description?: string
  icon?: string
  tags?: string[]
}

const SKILL_ICON_COMPONENTS: Record<string, Component> = {
  agent: Bot,
  bot: Bot,
  browser: Globe,
  chrome: Globe,
  code: Code2,
  coding: Code2,
  document: FileText,
  docs: FileText,
  file: FileText,
  github: GitBranch,
  git: GitBranch,
  pdf: FileText,
  presentation: Presentation,
  slides: Presentation,
  spreadsheet: Table2,
  sheet: Table2,
  table: Table2,
  tool: Wrench,
  utility: Wrench
}

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
  model: string
  modelOptions: string[]
  reasoningEffort: string
  reasoningOptions: string[]
  permissionMode: RuntimePermissionMode
  planModeEnabled: boolean
  skills: RuntimeSkillMetadata[]
  selectedSkillPaths: string[]
  skillsLoading: boolean
  skillErrors: RuntimeSkillLoadError[]
}>()

const emit = defineEmits<{
  send: [content: string]
  plan: [content: string]
  'toggle-plan-mode': []
  'toggle-skill': [skill: RuntimeSkillMetadata]
  'refresh-skills': [forceReload?: boolean]
  'update:model': [model: string]
  'update:reasoningEffort': [effort: string]
  'update:permissionMode': [mode: RuntimePermissionMode]
}>()

const draft = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const toolControlRef = ref<HTMLDivElement | null>(null)
const permissionControlRef = ref<HTMLDivElement | null>(null)
const isSubmitting = ref(false)
const toolMenuOpen = ref(false)
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
    description: '非危险工作区操作自动继续'
  },
  {
    value: 'full_access',
    label: '完全访问',
    description: '高风险：关闭沙箱并开启网络'
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
const selectedSkillPathSet = computed(
  () => new Set(props.selectedSkillPaths.map((path) => normalizeSkillPath(path)))
)
const selectedSkills = computed(() =>
  props.skills.filter((skill) => selectedSkillPathSet.value.has(normalizeSkillPath(skill.path)))
)
const visibleSkillErrors = computed(() => props.skillErrors.slice(0, 1))

function normalizeSkillPath(path: string): string {
  return path.trim().replace(/[\/]+$/, '')
}

function adjustTextareaHeight(): void {
  if (!textareaRef.value) return

  textareaRef.value.style.height = 'auto'
  const scrollHeight = textareaRef.value.scrollHeight
  const maxHeight = 200 // 最大高度约8行
  textareaRef.value.style.height = Math.min(scrollHeight, maxHeight) + 'px'
}

function formatModelLabel(model: string): string {
  const [provider, ...modelParts] = model.split('/')
  const modelName = modelParts.join('/')
  return modelName ? provider + ' · ' + modelName : model
}

function formatReasoningLabel(effort: string): string {
  return reasoningLabels[effort] || effort
}

function togglePermissionMenu(): void {
  if (props.disabled) return
  toolMenuOpen.value = false
  permissionMenuOpen.value = !permissionMenuOpen.value
}

function closeFloatingMenus(): void {
  toolMenuOpen.value = false
  permissionMenuOpen.value = false
}

function elementContainsTarget(element: HTMLElement | null, target: EventTarget | null): boolean {
  return Boolean(element && target instanceof Node && element.contains(target))
}

function handleDocumentPointerDown(event: PointerEvent): void {
  if (toolMenuOpen.value && !elementContainsTarget(toolControlRef.value, event.target)) {
    toolMenuOpen.value = false
  }

  if (
    permissionMenuOpen.value &&
    !elementContainsTarget(permissionControlRef.value, event.target)
  ) {
    permissionMenuOpen.value = false
  }
}

function handleDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeFloatingMenus()
  }
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
    textareaRef.value.style.height = 'auto'
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

function toggleToolMenu(): void {
  if (props.disabled) return
  permissionMenuOpen.value = false
  const nextOpen = !toolMenuOpen.value
  toolMenuOpen.value = nextOpen
  if (nextOpen && !props.skills.length && !props.skillErrors.length && !props.skillsLoading) {
    emit('refresh-skills')
  }
}

function isSkillSelected(skill: RuntimeSkillMetadata): boolean {
  return selectedSkillPathSet.value.has(normalizeSkillPath(skill.path))
}

function toggleSkill(skill: RuntimeSkillMetadata): void {
  if (props.disabled || isSubmitting.value) return
  emit('toggle-skill', skill)
}

function formatSkillDescription(skill: RuntimeSkillMetadata): string {
  return (skill.short_description || skill.description || '').trim()
}

function normalizeIconKey(value: string | undefined): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function explicitIconText(skill: SkillIconCarrier): string {
  return String(skill.icon || '').trim()
}

function skillIconComponent(skill: SkillIconCarrier): Component | null {
  const explicit = explicitIconText(skill)
  const explicitKey = normalizeIconKey(explicit)
  if (explicitKey && SKILL_ICON_COMPONENTS[explicitKey]) {
    return SKILL_ICON_COMPONENTS[explicitKey]
  }
  if (explicit && explicit.length <= 4) {
    return null
  }

  const searchable = [skill.name, skill.description, skill.short_description, ...(skill.tags || [])]
    .join(' ')
    .toLowerCase()

  for (const [key, component] of Object.entries(SKILL_ICON_COMPONENTS)) {
    if (searchable.includes(key)) return component
  }
  return Blocks
}

function skillIconText(skill: SkillIconCarrier): string {
  const explicit = explicitIconText(skill)
  const compact = skill.name.trim().replace(/\s+/g, '')
  return explicit || (compact ? compact.slice(0, 2).toUpperCase() : 'SK')
}

function sendMessage(): void {
  if (props.disabled || isSubmitting.value) return

  const text = draft.value.trim()
  if (!text) {
    clearDraft()
    return
  }

  isSubmitting.value = true
  toolMenuOpen.value = false
  permissionMenuOpen.value = false
  clearDraft()
  if (props.planModeEnabled) {
    emit('plan', text)
  } else {
    emit('send', text)
  }
  unlockSubmitSoon()
}

function togglePlanMode(): void {
  if (props.disabled || isSubmitting.value) return
  emit('toggle-plan-mode')
}

function handleEnter(event: KeyboardEvent): void {
  if (event.isComposing) return

  event.preventDefault()
  sendMessage()
}

watch(draft, () => {
  adjustTextareaHeight()
})

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  if (submitUnlockTimer) {
    window.clearTimeout(submitUnlockTimer)
  }
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
})
</script>

<template>
  <form class="composer" @submit.prevent="sendMessage">
    <div class="composer-input-area">
      <div v-if="selectedSkills.length" class="composer-selected-plugins" aria-label="已选择插件">
        <button
          v-for="skill in selectedSkills"
          :key="skill.path"
          class="composer-selected-plugin"
          type="button"
          :title="'取消选择 ' + skill.name"
          :disabled="disabled || isSubmitting"
          @click.stop="toggleSkill(skill)"
        >
          <span class="composer-selected-plugin-icon">
            <component v-if="skillIconComponent(skill)" :is="skillIconComponent(skill)" />
            <span v-else>{{ skillIconText(skill) }}</span>
          </span>
          <span class="composer-selected-plugin-name">{{ skill.name }}</span>
          <X class="composer-selected-plugin-remove" />
        </button>
      </div>
      <textarea
        ref="textareaRef"
        v-model="draft"
        :placeholder="placeholder || '输入消息...'"
        :disabled="disabled || isSubmitting"
        rows="1"
        @keydown.enter.exact="handleEnter"
        @input="adjustTextareaHeight"
      ></textarea>
    </div>

    <div class="composer-actions">
      <div class="left-tools">
        <div ref="toolControlRef" class="composer-tool-control">
          <IconButton
            class="composer-plus-button"
            label="打开工具菜单"
            size="lg"
            :aria-expanded="toolMenuOpen"
            :disabled="disabled || isSubmitting"
            @click="toggleToolMenu"
          >
            <Plus />
          </IconButton>
          <div v-if="toolMenuOpen" class="composer-tool-menu">
            <div class="composer-tool-menu-title">Add</div>
            <button
              class="composer-tool-option"
              type="button"
              :aria-pressed="planModeEnabled"
              @click.stop="togglePlanMode"
            >
              <span class="composer-tool-option-main">
                <ListChecks />
                <span>计划模式</span>
              </span>
              <span class="composer-switch" :class="{ active: planModeEnabled }">
                <span></span>
              </span>
            </button>
            <div class="composer-tool-section-label">插件</div>
            <div v-if="skillsLoading" class="composer-plugin-empty">正在加载</div>
            <template v-else-if="skills.length">
              <button
                v-for="skill in skills"
                :key="skill.path"
                class="composer-plugin-option"
                :class="{ selected: isSkillSelected(skill) }"
                type="button"
                :aria-pressed="isSkillSelected(skill)"
                @click.stop="toggleSkill(skill)"
              >
                <span class="composer-plugin-icon">
                  <component v-if="skillIconComponent(skill)" :is="skillIconComponent(skill)" />
                  <span v-else>{{ skillIconText(skill) }}</span>
                </span>
                <span class="composer-plugin-copy">
                  <span>{{ skill.name }}</span>
                  <small>{{ formatSkillDescription(skill) }}</small>
                </span>
                <Check v-if="isSkillSelected(skill)" class="composer-plugin-check" />
              </button>
            </template>
            <div v-else class="composer-plugin-empty">暂无插件</div>
            <div v-if="visibleSkillErrors.length" class="composer-plugin-empty warning">
              {{ visibleSkillErrors[0].message }}
            </div>
          </div>
        </div>
        <div ref="permissionControlRef" class="permission-mode-control">
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

      <IconButton
        class="send-button"
        type="submit"
        label="发送消息"
        size="lg"
        :disabled="disabled || isSubmitting"
      >
        <SendHorizontal />
      </IconButton>
    </div>
  </form>
</template>
