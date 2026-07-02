<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Plus, RefreshCw, Trash2, X } from '@lucide/vue'
import type {
  RuntimeSkillDraft,
  RuntimeSkillImportDraft,
  RuntimeSkillInstallDraft,
  RuntimeSkillMetadata,
  RuntimeSkillResource,
  RuntimeSkillResourceDraft
} from '@renderer/types/runtimeEvents'

type SkillEditorMode = 'create' | 'edit' | 'import' | 'install'
type SkillPackageTemplate = 'basic' | 'standard'

const props = defineProps<{
  mode: SkillEditorMode
  disabled?: boolean
  skillEditorLoading: boolean
  initialSkill: RuntimeSkillMetadata | null
  skillEditorSkill: RuntimeSkillMetadata | null
  skillEditorContent: string
  skillManagementError: string
  skillResources: RuntimeSkillResource[]
  skillResourcesLoading: boolean
  skillResourceLoading: boolean
  skillResourcePath: string
  skillResourceContent: string
  skillResourceError: string
}>()

const emit = defineEmits<{
  'save-skill': [draft: RuntimeSkillDraft]
  'import-skill': [draft: RuntimeSkillImportDraft]
  'install-skill': [draft: RuntimeSkillInstallDraft]
  'refresh-skill-resources': []
  'load-skill-resource': [resource: RuntimeSkillResource]
  'save-skill-resource': [draft: RuntimeSkillResourceDraft]
  'delete-skill-resource': [resource: RuntimeSkillResource]
  close: []
}>()

const defaultBody = '## Instructions\n\n- Describe when and how to use this skill.'
const standardPackageBody =
  '## Instructions\n\n' +
  '- Describe when this skill should be used.\n' +
  '- Read references/README.md before applying detailed guidance.'

const skillEditorScope = ref<'repo' | 'user'>('repo')
const skillEditorTemplate = ref<SkillPackageTemplate>('basic')
const skillEditorPath = ref('')
const skillImportSourcePath = ref('')
const skillEditorName = ref('')
const skillEditorDescription = ref('')
const skillEditorShortDescription = ref('')
const skillEditorIcon = ref('')
const skillEditorAllowImplicit = ref(true)
const skillEditorBody = ref('')
const skillResourceEditorOpen = ref(false)
const skillResourceDraftPath = ref('')
const skillResourceDraftContent = ref('')

const skillEditorTitle = computed(() => {
  if (props.mode === 'create') return '新建 Skill'
  if (props.mode === 'import') return '导入 Skill'
  if (props.mode === 'install') return '安装 Skill'
  return '编辑 Skill'
})
const skillEditorCanSave = computed(
  () =>
    !props.disabled &&
    !props.skillEditorLoading &&
    Boolean(skillEditorName.value.trim()) &&
    Boolean(skillEditorDescription.value.trim())
)
const skillEditorCanImport = computed(
  () => !props.disabled && !props.skillEditorLoading && Boolean(skillImportSourcePath.value.trim())
)
const skillEditorCanSubmit = computed(() =>
  props.mode === 'import' || props.mode === 'install'
    ? skillEditorCanImport.value
    : skillEditorCanSave.value
)
const skillResourceCanSave = computed(
  () =>
    props.mode === 'edit' &&
    Boolean(skillEditorPath.value) &&
    Boolean(skillResourceDraftPath.value.trim()) &&
    !props.skillResourceLoading
)

function skillAllowsImplicit(skill: RuntimeSkillMetadata): boolean {
  return skill.policy?.allow_implicit_invocation !== false
}

function formatResourceSize(size: number): string {
  if (size < 1024) return size + ' B'
  return Math.ceil(size / 1024) + ' KB'
}

function bodyForTemplate(template: SkillPackageTemplate): string {
  return template === 'standard' ? standardPackageBody : defaultBody
}

function applySkill(skill: RuntimeSkillMetadata, content: string): void {
  skillEditorScope.value = skill.scope === 'user' ? 'user' : 'repo'
  skillEditorPath.value = skill.path
  skillEditorName.value = skill.name
  skillEditorDescription.value = skill.description
  skillEditorShortDescription.value = skill.short_description || ''
  skillEditorIcon.value = skill.icon || ''
  skillEditorAllowImplicit.value = skillAllowsImplicit(skill)
  skillEditorBody.value = content
}

function resetForMode(): void {
  if (props.mode === 'create') {
    skillEditorScope.value = 'repo'
    skillEditorTemplate.value = 'basic'
    skillEditorPath.value = ''
    skillImportSourcePath.value = ''
    skillEditorName.value = ''
    skillEditorDescription.value = ''
    skillEditorShortDescription.value = ''
    skillEditorIcon.value = ''
    skillEditorAllowImplicit.value = true
    skillEditorBody.value = bodyForTemplate(skillEditorTemplate.value)
    closeResourceEditor()
    return
  }

  if (props.mode === 'import' || props.mode === 'install') {
    skillEditorScope.value = props.mode === 'install' ? 'user' : 'repo'
    skillEditorPath.value = ''
    skillImportSourcePath.value = ''
    skillEditorIcon.value = ''
    closeResourceEditor()
    return
  }

  const loadedSkill = props.skillEditorSkill
  const initialSkill = props.initialSkill
  if (loadedSkill && (!initialSkill || loadedSkill.path === initialSkill.path)) {
    applySkill(loadedSkill, props.skillEditorContent)
    return
  }
  if (initialSkill) {
    applySkill(initialSkill, '')
  }
}

function openNewResource(): void {
  if (props.mode !== 'edit') return
  skillResourceEditorOpen.value = true
  skillResourceDraftPath.value = 'references/guide.md'
  skillResourceDraftContent.value = ''
}

function closeResourceEditor(): void {
  skillResourceEditorOpen.value = false
  skillResourceDraftPath.value = ''
  skillResourceDraftContent.value = ''
}

function selectResource(resource: RuntimeSkillResource): void {
  skillResourceEditorOpen.value = true
  skillResourceDraftPath.value = resource.resource
  skillResourceDraftContent.value =
    props.skillResourcePath === resource.resource ? props.skillResourceContent : ''
  emit('load-skill-resource', resource)
}

function saveResource(): void {
  if (!skillResourceCanSave.value) return
  emit('save-skill-resource', {
    skill_path: skillEditorPath.value,
    resource: skillResourceDraftPath.value.trim(),
    content: skillResourceDraftContent.value
  })
}

function deleteResource(resource: RuntimeSkillResource): void {
  if (props.skillResourceLoading) return
  if (!window.confirm('删除 Skill 资源：' + resource.resource + '？')) return
  emit('delete-skill-resource', resource)
}

function submitSkillEditor(): void {
  if (props.mode === 'import' || props.mode === 'install') {
    if (!skillEditorCanImport.value) return
    if (props.mode === 'install') {
      emit('install-skill', {
        scope: skillEditorScope.value,
        source_type: 'github',
        source: skillImportSourcePath.value.trim()
      })
      return
    }
    emit('import-skill', {
      scope: skillEditorScope.value,
      source_path: skillImportSourcePath.value.trim()
    })
    return
  }

  if (!skillEditorCanSave.value) return
  emit('save-skill', {
    path: skillEditorPath.value || undefined,
    scope: skillEditorScope.value,
    package_template: props.mode === 'create' ? skillEditorTemplate.value : undefined,
    name: skillEditorName.value.trim(),
    description: skillEditorDescription.value.trim(),
    short_description: skillEditorShortDescription.value.trim() || undefined,
    icon: skillEditorIcon.value.trim() || undefined,
    allow_implicit_invocation: skillEditorAllowImplicit.value,
    content: skillEditorBody.value
  })
}

watch(
  () => [props.mode, props.initialSkill?.path] as const,
  () => resetForMode(),
  { immediate: true }
)

watch(
  () => props.skillEditorSkill,
  (skill) => {
    if (props.mode !== 'edit' || !skill) return
    if (skillEditorPath.value && skill.path !== skillEditorPath.value) return
    applySkill(skill, props.skillEditorContent)
  }
)

watch(
  () => props.skillEditorContent,
  (content) => {
    if (props.mode !== 'edit' || !props.skillEditorSkill) return
    if (props.skillEditorSkill.path !== skillEditorPath.value) return
    skillEditorBody.value = content
  }
)

watch(
  () => [props.skillResourcePath, props.skillResourceContent] as const,
  ([resourcePath, content]) => {
    if (!resourcePath) return
    skillResourceEditorOpen.value = true
    skillResourceDraftPath.value = resourcePath
    skillResourceDraftContent.value = content
  }
)

watch(skillEditorTemplate, (template, previous) => {
  if (props.mode !== 'create') return
  const previousBody = bodyForTemplate(previous || 'basic')
  if (skillEditorBody.value.trim() === previousBody.trim()) {
    skillEditorBody.value = bodyForTemplate(template)
  }
})
</script>

<template>
  <div class="composer-skill-editor">
    <div class="composer-skill-editor-head">
      <strong>{{ skillEditorTitle }}</strong>
      <button type="button" aria-label="关闭 Skill 编辑器" @click.stop="emit('close')">
        <X />
      </button>
    </div>
    <div class="composer-skill-editor-grid">
      <label v-if="mode === 'create' || mode === 'import' || mode === 'install'">
        <span>范围</span>
        <select v-model="skillEditorScope" :disabled="skillEditorLoading">
          <option value="repo">repo</option>
          <option value="user">user</option>
        </select>
      </label>
      <label v-if="mode === 'create'">
        <span>模板</span>
        <select v-model="skillEditorTemplate" :disabled="skillEditorLoading">
          <option value="basic">basic</option>
          <option value="standard">standard package</option>
        </select>
      </label>
      <label v-if="mode === 'import' || mode === 'install'">
        <span>{{ mode === 'install' ? 'GitHub' : '来源' }}</span>
        <input
          v-model="skillImportSourcePath"
          type="text"
          autocomplete="off"
          :disabled="skillEditorLoading"
          :placeholder="mode === 'install' ? 'https://github.com/owner/repo/tree/main/path' : '/path/to/skill-or-SKILL.md'"
          @keydown.enter.prevent.stop
        />
      </label>
      <label v-if="mode !== 'import'">
        <span>名称</span>
        <input
          v-model="skillEditorName"
          type="text"
          autocomplete="off"
          :disabled="skillEditorLoading"
          placeholder="demo-skill"
          @keydown.enter.prevent.stop
        />
      </label>
      <label v-if="mode !== 'import'">
        <span>描述</span>
        <textarea
          v-model="skillEditorDescription"
          rows="2"
          :disabled="skillEditorLoading"
        ></textarea>
      </label>
      <label v-if="mode !== 'import'">
        <span>短描述</span>
        <input
          v-model="skillEditorShortDescription"
          type="text"
          autocomplete="off"
          :disabled="skillEditorLoading"
          @keydown.enter.prevent.stop
        />
      </label>
      <label v-if="mode !== 'import'">
        <span>图标</span>
        <input
          v-model="skillEditorIcon"
          type="text"
          autocomplete="off"
          :disabled="skillEditorLoading"
          placeholder="pdf / github / browser"
          @keydown.enter.prevent.stop
        />
      </label>
      <label v-if="mode !== 'import'" class="composer-skill-checkbox">
        <input
          v-model="skillEditorAllowImplicit"
          type="checkbox"
          :disabled="skillEditorLoading"
        />
        <span>隐式推荐</span>
      </label>
      <label v-if="mode !== 'import'">
        <span>正文</span>
        <textarea
          v-model="skillEditorBody"
          class="composer-skill-body"
          rows="6"
          :disabled="skillEditorLoading"
        ></textarea>
      </label>
    </div>
    <div v-if="mode === 'edit'" class="composer-skill-resource-panel">
      <div class="composer-skill-resource-head">
        <strong>资源</strong>
        <span>
          <button
            type="button"
            aria-label="刷新 Skill 资源"
            :disabled="skillResourcesLoading"
            @click.stop="emit('refresh-skill-resources')"
          >
            <RefreshCw :class="{ spinning: skillResourcesLoading }" />
          </button>
          <button type="button" aria-label="新增 Skill 资源" @click.stop="openNewResource">
            <Plus />
          </button>
        </span>
      </div>
      <div class="composer-skill-resource-list">
        <div v-if="skillResourcesLoading" class="composer-skill-resource-empty">正在加载</div>
        <template v-else>
          <div
            v-for="resource in skillResources"
            :key="resource.resource"
            class="composer-skill-resource-row"
          >
            <button
              class="composer-skill-resource-item"
              :class="{ selected: resource.resource === skillResourceDraftPath }"
              type="button"
              @click.stop="selectResource(resource)"
            >
              <span>{{ resource.resource }}</span>
              <small>{{ formatResourceSize(resource.size) }}</small>
            </button>
            <button
              class="composer-skill-resource-delete"
              type="button"
              aria-label="删除 Skill 资源"
              :disabled="skillResourceLoading"
              @click.stop="deleteResource(resource)"
            >
              <Trash2 />
            </button>
          </div>
        </template>
        <div v-if="!skillResourcesLoading && !skillResources.length" class="composer-skill-resource-empty">
          暂无资源
        </div>
      </div>
      <div v-if="skillResourceEditorOpen" class="composer-skill-resource-editor">
        <label>
          <span>路径</span>
          <input
            v-model="skillResourceDraftPath"
            type="text"
            autocomplete="off"
            :disabled="skillResourceLoading"
            placeholder="references/guide.md"
            @keydown.enter.prevent.stop
          />
        </label>
        <label>
          <span>内容</span>
          <textarea
            v-model="skillResourceDraftContent"
            rows="5"
            :disabled="skillResourceLoading"
          ></textarea>
        </label>
        <div v-if="skillResourceError" class="composer-skill-errors">
          {{ skillResourceError }}
        </div>
        <div class="composer-skill-resource-actions">
          <button type="button" @click.stop="closeResourceEditor">关闭</button>
          <button
            type="button"
            class="primary"
            :disabled="!skillResourceCanSave"
            @click.stop="saveResource"
          >
            {{ skillResourceLoading ? '处理中' : '保存资源' }}
          </button>
        </div>
      </div>
    </div>
    <div v-if="skillManagementError" class="composer-skill-errors">
      {{ skillManagementError }}
    </div>
    <div class="composer-skill-editor-actions">
      <button type="button" @click.stop="emit('close')">取消</button>
      <button
        type="button"
        class="primary"
        :disabled="!skillEditorCanSubmit"
        @click.stop="submitSkillEditor"
      >
        {{ skillEditorLoading ? '处理中' : mode === 'install' ? '安装' : mode === 'import' ? '导入' : '保存' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer-skill-editor {
  display: grid;
  gap: 9px;
  border-top: 1px solid var(--border-subtle);
  padding: 8px 4px 2px;
}

.composer-skill-editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 4px;
}

.composer-skill-editor-head strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 800;
}

.composer-skill-editor-head button {
  width: 26px;
  height: 26px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.composer-skill-editor-head button:hover {
  background: var(--surface-hover);
}

.composer-skill-editor-head svg {
  width: 14px;
  height: 14px;
}

.composer-skill-editor-grid {
  display: grid;
  gap: 7px;
}

.composer-skill-editor-grid label {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.composer-skill-editor-grid label > span,
.composer-skill-checkbox span {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
}

.composer-skill-editor-grid input,
.composer-skill-editor-grid select,
.composer-skill-editor-grid textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--border-control);
  border-radius: 9px;
  padding: 7px 9px;
  color: var(--text-primary);
  background: var(--field-bg);
  font: inherit;
  font-size: 12px;
  line-height: 1.35;
  outline: 0;
}

.composer-skill-editor-grid textarea {
  resize: vertical;
}

.composer-skill-editor-grid input:focus,
.composer-skill-editor-grid select:focus,
.composer-skill-editor-grid textarea:focus {
  border-color: var(--accent-border-soft);
  box-shadow: 0 0 0 2px var(--accent-focus);
}

.composer-skill-checkbox {
  display: inline-flex !important;
  grid-template-columns: none !important;
  align-items: center;
  gap: 7px !important;
}

.composer-skill-checkbox input {
  width: 15px;
  height: 15px;
  margin: 0;
}

.composer-skill-body {
  max-height: 160px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
}

.composer-skill-errors {
  display: grid;
  gap: 3px;
  border-top: 1px solid var(--border-subtle);
  padding: 7px 8px 3px;
  color: var(--warning-strong);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
}

.composer-skill-resource-panel {
  display: grid;
  gap: 7px;
  border-top: 1px solid var(--border-subtle);
  padding: 8px 4px 2px;
}

.composer-skill-resource-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 4px;
}

.composer-skill-resource-head strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 800;
}

.composer-skill-resource-head span {
  display: inline-flex;
  gap: 4px;
}

.composer-skill-resource-head button,
.composer-skill-resource-delete {
  width: 26px;
  height: 26px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.composer-skill-resource-head button:hover,
.composer-skill-resource-delete:hover {
  background: var(--surface-hover);
}

.composer-skill-resource-head button:disabled,
.composer-skill-resource-delete:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.composer-skill-resource-head svg,
.composer-skill-resource-delete svg {
  width: 14px;
  height: 14px;
}

.composer-skill-resource-head .spinning {
  animation: skill-editor-spin 0.9s linear infinite;
}

@keyframes skill-editor-spin {
  to {
    transform: rotate(360deg);
  }
}

.composer-skill-resource-list {
  max-height: 128px;
  display: grid;
  gap: 3px;
  overflow-y: auto;
}

.composer-skill-resource-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px;
  gap: 3px;
}

.composer-skill-resource-item {
  min-width: 0;
  display: grid;
  gap: 2px;
  border: 0;
  border-radius: 9px;
  padding: 7px 9px;
  color: var(--text-primary);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.composer-skill-resource-item:hover,
.composer-skill-resource-item.selected {
  background: var(--surface-hover);
}

.composer-skill-resource-item span {
  overflow: hidden;
  font-size: 12px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-skill-resource-item small,
.composer-skill-resource-empty {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 700;
}

.composer-skill-resource-empty {
  padding: 7px 9px;
}

.composer-skill-resource-editor {
  display: grid;
  gap: 7px;
}

.composer-skill-resource-editor label {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.composer-skill-resource-editor label > span {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
}

.composer-skill-resource-editor input,
.composer-skill-resource-editor textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--border-control);
  border-radius: 9px;
  padding: 7px 9px;
  color: var(--text-primary);
  background: var(--field-bg);
  font: inherit;
  font-size: 12px;
  line-height: 1.35;
  outline: 0;
}

.composer-skill-resource-editor textarea {
  resize: vertical;
}

.composer-skill-resource-editor input:focus,
.composer-skill-resource-editor textarea:focus {
  border-color: var(--accent-border-soft);
  box-shadow: 0 0 0 2px var(--accent-focus);
}

.composer-skill-resource-actions {
  display: flex;
  justify-content: flex-end;
  gap: 7px;
}

.composer-skill-resource-actions button {
  border: 0;
  border-radius: 9px;
  padding: 7px 11px;
  color: var(--text-primary);
  background: var(--surface-hover);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.composer-skill-resource-actions button.primary {
  color: var(--text-on-primary);
  background: var(--accent);
}

.composer-skill-resource-actions button:disabled {
  color: var(--text-disabled);
  background: var(--surface-subtle);
  cursor: default;
}

.composer-skill-editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 7px;
}

.composer-skill-editor-actions button {
  border: 0;
  border-radius: 9px;
  padding: 7px 11px;
  color: var(--text-primary);
  background: var(--surface-hover);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.composer-skill-editor-actions button.primary {
  color: var(--text-on-primary);
  background: var(--accent);
}

.composer-skill-editor-actions button:disabled {
  color: var(--text-disabled);
  background: var(--surface-subtle);
  cursor: default;
}
</style>
