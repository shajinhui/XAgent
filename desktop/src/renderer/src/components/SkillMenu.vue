<script setup lang="ts">
import { computed, ref, watch, type Component } from 'vue'
import {
  Blocks,
  Bot,
  Check,
  Code2,
  Download,
  FileText,
  GitBranch,
  Globe,
  PencilLine,
  Plus,
  Presentation,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Table2,
  Trash2,
  Upload,
  Wrench
} from '@lucide/vue'
import SkillEditor from '@renderer/components/SkillEditor.vue'
import type {
  RuntimeInstallableSkill,
  RuntimeSkillDraft,
  RuntimeSkillImportDraft,
  RuntimeSkillInstallDraft,
  RuntimeSkillLoadError,
  RuntimeSkillMetadata,
  RuntimeSkillResource,
  RuntimeSkillResourceDraft,
  RuntimeSkillRegistryError
} from '@renderer/types/runtimeEvents'

type SkillEditorMode = 'closed' | 'create' | 'edit' | 'import' | 'install'
type ActiveSkillEditorMode = Exclude<SkillEditorMode, 'closed'>
type SkillDependencyCarrier = {
  dependency_status?: {
    missing_tools?: string[]
  }
}
type SkillGroup = {
  id: string
  title: string
  skills: RuntimeSkillMetadata[]
}
type SkillFilterMode = 'all' | 'repo' | 'user' | 'installable'
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
  skills: RuntimeSkillMetadata[]
  selectedSkillPaths: string[]
  skillsLoading: boolean
  skillErrors: RuntimeSkillLoadError[]
  installableSkills: RuntimeInstallableSkill[]
  installableSkillErrors: RuntimeSkillRegistryError[]
  installableSkillsLoading: boolean
  skillEditorLoading: boolean
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
  'toggle-skill': [skill: RuntimeSkillMetadata]
  'refresh-skills': [forceReload?: boolean]
  'refresh-installable-skills': [forceReload?: boolean]
  'edit-skill': [skill: RuntimeSkillMetadata]
  'save-skill': [draft: RuntimeSkillDraft]
  'import-skill': [draft: RuntimeSkillImportDraft]
  'install-skill': [draft: RuntimeSkillInstallDraft]
  'install-registry-skill': [skill: RuntimeInstallableSkill]
  'reinstall-registry-skill': [skill: RuntimeInstallableSkill]
  'reinstall-skill': [skill: RuntimeSkillMetadata]
  'delete-skill': [skill: RuntimeSkillMetadata]
  'clear-skill-editor': []
  'refresh-skill-resources': [skill: RuntimeSkillMetadata]
  'load-skill-resource': [skill: RuntimeSkillMetadata, resource: RuntimeSkillResource]
  'save-skill-resource': [draft: RuntimeSkillResourceDraft]
  'delete-skill-resource': [skill: RuntimeSkillMetadata, resource: RuntimeSkillResource]
}>()

const skillEditorMode = ref<SkillEditorMode>('closed')
const activeEditSkill = ref<RuntimeSkillMetadata | null>(null)
const skillMutationPending = ref(false)
const searchQuery = ref('')
const skillFilterMode = ref<SkillFilterMode>('all')

const activeEditorMode = computed<ActiveSkillEditorMode | null>(() =>
  skillEditorMode.value === 'closed' ? null : skillEditorMode.value
)
const selectedSkillPathSet = computed(
  () => new Set(props.selectedSkillPaths.map((path) => normalizeSkillPath(path)))
)
const selectedSkillCount = computed(() => selectedSkillPathSet.value.size)
const normalizedSearchQuery = computed(() => searchQuery.value.trim().toLowerCase())
const filteredSkills = computed(() =>
  props.skills.filter(
    (skill) =>
      skillMatchesFilter(skill) &&
      matchesSearch([skill.name, skill.description, skill.short_description, skill.scope, skill.path])
  )
)
const skillGroups = computed<SkillGroup[]>(() =>
  [
    {
      id: 'repo',
      title: '工作区',
      skills: filteredSkills.value.filter((skill) => skill.scope === 'repo')
    },
    {
      id: 'user',
      title: '个人',
      skills: filteredSkills.value.filter((skill) => skill.scope === 'user')
    },
    {
      id: 'other',
      title: '其他',
      skills: filteredSkills.value.filter((skill) => skill.scope !== 'repo' && skill.scope !== 'user')
    }
  ].filter((group) => group.skills.length)
)
const installedPreviewSkills = computed(() => filteredSkills.value.slice(0, 8))
const hiddenInstalledSkillCount = computed(() =>
  Math.max(0, filteredSkills.value.length - installedPreviewSkills.value.length)
)
const filteredInstallableSkills = computed(() =>
  props.installableSkills.filter(
    (skill) =>
      (skillFilterMode.value === 'all' || skillFilterMode.value === 'installable') &&
      matchesSearch([
        skill.name,
        skill.description,
        skill.source_type,
        skill.version,
        skill.installed_version,
        skill.tags.join(' ')
      ])
  )
)
const visibleSkillErrors = computed(() => props.skillErrors.slice(0, 2))
const visibleInstallableSkillErrors = computed(() => props.installableSkillErrors.slice(0, 2))

function normalizeSkillPath(path: string): string {
  return path.trim().replace(/[\\/]+$/, '')
}

function matchesSearch(values: Array<string | undefined | null>): boolean {
  const query = normalizedSearchQuery.value
  if (!query) return true
  return values.some((value) => value?.toLowerCase().includes(query))
}

function skillMatchesFilter(skill: RuntimeSkillMetadata): boolean {
  if (skillFilterMode.value === 'all') return true
  return skill.scope === skillFilterMode.value
}

function selectSkillFilter(mode: SkillFilterMode): void {
  skillFilterMode.value = mode
}

function resetSkillFilters(): void {
  searchQuery.value = ''
  skillFilterMode.value = 'all'
}

function formatSkillInitials(name: string): string {
  const compact = name.trim().replace(/\s+/g, '')
  return compact ? compact.slice(0, 2).toUpperCase() : 'SK'
}

function normalizeIconKey(value: string | undefined): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
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

  const searchable = [
    skill.name,
    skill.description,
    skill.short_description,
    ...(skill.tags || [])
  ]
    .join(' ')
    .toLowerCase()

  for (const [key, component] of Object.entries(SKILL_ICON_COMPONENTS)) {
    if (searchable.includes(key)) return component
  }
  return Blocks
}

function skillIconText(skill: SkillIconCarrier): string {
  return explicitIconText(skill) || formatSkillInitials(skill.name)
}

function formatSkillDescription(skill: RuntimeSkillMetadata): string {
  return (skill.short_description || skill.description || '').trim()
}

function formatSkillScope(scope: string): string {
  if (scope === 'repo') return '工作区'
  if (scope === 'user') return '个人'
  return scope || 'skill'
}

function formatInstallableTags(skill: RuntimeInstallableSkill): string {
  const parts = [...skill.tags.slice(0, 3)]
  if (skill.version) parts.unshift('v' + skill.version)
  if (skill.installed_version) parts.push('当前 v' + skill.installed_version)
  return parts.join(' · ')
}

function formatInstallableState(skill: RuntimeInstallableSkill): string {
  if (skill.update_available) return '可更新'
  if (skill.installed) return '已安装'
  return skill.source_type
}

function formatMissingTools(skill: SkillDependencyCarrier): string {
  const missing = skill.dependency_status?.missing_tools || []
  return missing.slice(0, 3).join(', ')
}

function isSkillSelected(skill: RuntimeSkillMetadata): boolean {
  return selectedSkillPathSet.value.has(normalizeSkillPath(skill.path))
}

function toggleSkill(skill: RuntimeSkillMetadata): void {
  if (props.disabled) return
  emit('toggle-skill', skill)
}

function refreshSkills(forceReload = false): void {
  if (props.disabled) return
  emit('refresh-skills', forceReload)
}

function refreshInstallableSkills(forceReload = false): void {
  if (props.disabled) return
  emit('refresh-installable-skills', forceReload)
}

function openCreateSkill(): void {
  if (props.disabled) return
  skillEditorMode.value = 'create'
  activeEditSkill.value = null
  skillMutationPending.value = false
  emit('clear-skill-editor')
}

function openImportSkill(): void {
  if (props.disabled) return
  skillEditorMode.value = 'import'
  activeEditSkill.value = null
  skillMutationPending.value = false
  emit('clear-skill-editor')
}

function openInstallSkill(): void {
  if (props.disabled) return
  skillEditorMode.value = 'install'
  activeEditSkill.value = null
  skillMutationPending.value = false
  emit('clear-skill-editor')
}

function openEditSkill(skill: RuntimeSkillMetadata): void {
  if (props.disabled) return
  skillEditorMode.value = 'edit'
  activeEditSkill.value = skill
  skillMutationPending.value = false
  emit('edit-skill', skill)
}

function closeSkillEditor(): void {
  skillEditorMode.value = 'closed'
  activeEditSkill.value = null
  skillMutationPending.value = false
  emit('clear-skill-editor')
}

function saveSkill(draft: RuntimeSkillDraft): void {
  skillMutationPending.value = true
  emit('save-skill', draft)
}

function importSkill(draft: RuntimeSkillImportDraft): void {
  skillMutationPending.value = true
  emit('import-skill', draft)
}

function installSkill(draft: RuntimeSkillInstallDraft): void {
  skillMutationPending.value = true
  emit('install-skill', draft)
}

function installRegistrySkill(skill: RuntimeInstallableSkill): void {
  if (props.disabled || props.skillEditorLoading) return
  if (skill.installed) return
  if (!window.confirm('安装 Skill：' + skill.name + '？')) return
  skillMutationPending.value = true
  emit('install-registry-skill', skill)
}

function reinstallRegistrySkill(skill: RuntimeInstallableSkill): void {
  if (props.disabled || props.skillEditorLoading || !skill.installed_path) return
  if (!window.confirm('更新 Skill：' + skill.name + '？')) return
  skillMutationPending.value = true
  emit('reinstall-registry-skill', skill)
}

function submitInstallableSkill(skill: RuntimeInstallableSkill): void {
  if (skill.update_available && skill.installed_path) {
    reinstallRegistrySkill(skill)
    return
  }
  installRegistrySkill(skill)
}

function installableSkillActionDisabled(skill: RuntimeInstallableSkill): boolean {
  if (props.disabled) return true
  if (props.skillEditorLoading) return true
  if (skill.update_available && skill.installed_path) return false
  return Boolean(skill.installed)
}

function deleteSkill(skill: RuntimeSkillMetadata): void {
  if (props.disabled || props.skillEditorLoading) return
  if (!window.confirm('删除 Skill：' + skill.name + '？')) return
  skillMutationPending.value = true
  emit('delete-skill', skill)
}

function canReinstallSkill(skill: RuntimeSkillMetadata): boolean {
  return skill.install?.source_type === 'github'
}

function reinstallSkill(skill: RuntimeSkillMetadata): void {
  if (props.disabled || props.skillEditorLoading || !canReinstallSkill(skill)) return
  if (!window.confirm('重新安装 Skill：' + skill.name + '？')) return
  skillMutationPending.value = true
  emit('reinstall-skill', skill)
}

function refreshSkillResources(): void {
  if (!activeEditSkill.value) return
  emit('refresh-skill-resources', activeEditSkill.value)
}

function loadSkillResource(resource: RuntimeSkillResource): void {
  if (!activeEditSkill.value) return
  emit('load-skill-resource', activeEditSkill.value, resource)
}

function saveSkillResource(draft: RuntimeSkillResourceDraft): void {
  emit('save-skill-resource', draft)
}

function deleteSkillResource(resource: RuntimeSkillResource): void {
  if (!activeEditSkill.value) return
  emit('delete-skill-resource', activeEditSkill.value, resource)
}

watch(
  () => props.skillEditorLoading,
  (loading, previous) => {
    if (!previous || loading || !skillMutationPending.value) return
    skillMutationPending.value = false
    if (!props.skillManagementError) {
      skillEditorMode.value = 'closed'
      activeEditSkill.value = null
    }
  }
)
</script>

<template>
  <div class="composer-skill-menu skill-hub">
    <div class="skill-hub-tabs" role="tablist" aria-label="插件类型">
      <button type="button" role="tab" title="插件市场后续接入" disabled>插件</button>
      <button type="button" class="active" role="tab" aria-selected="true">技能</button>
    </div>

    <header class="skill-hub-hero">
      <h2>技能</h2>
      <p>通过任务专用技能扩展 XCode 的能力</p>
      <div class="skill-hub-search-row">
        <label class="skill-hub-search">
          <Search />
          <span class="sr-only">搜索插件和技能</span>
          <input v-model="searchQuery" type="search" placeholder="搜索插件和技能" />
        </label>
        <button
          class="skill-hub-filter"
          type="button"
          title="重置筛选"
          :disabled="!searchQuery && skillFilterMode === 'all'"
          @click.stop="resetSkillFilters"
        >
          <SlidersHorizontal />
        </button>
      </div>
      <div class="skill-hub-filter-chips" role="group" aria-label="Skill 筛选">
        <button
          type="button"
          :class="{ active: skillFilterMode === 'all' }"
          @click.stop="selectSkillFilter('all')"
        >
          全部
        </button>
        <button
          type="button"
          :class="{ active: skillFilterMode === 'repo' }"
          @click.stop="selectSkillFilter('repo')"
        >
          工作区
        </button>
        <button
          type="button"
          :class="{ active: skillFilterMode === 'user' }"
          @click.stop="selectSkillFilter('user')"
        >
          个人
        </button>
        <button
          type="button"
          :class="{ active: skillFilterMode === 'installable' }"
          @click.stop="selectSkillFilter('installable')"
        >
          可安装
        </button>
      </div>
    </header>

    <section v-if="skillFilterMode !== 'installable'" class="skill-hub-section">
      <div class="skill-hub-section-head">
        <h3>已添加</h3>
        <span v-if="selectedSkillCount" class="skill-hub-selected-count">
          本轮已选 {{ selectedSkillCount }}
        </span>
        <div class="skill-hub-manage-actions" aria-label="Skill 管理">
          <button type="button" title="刷新 Skills" :disabled="skillsLoading" @click.stop="refreshSkills(true)">
            <RefreshCw :class="{ spinning: skillsLoading }" />
          </button>
          <button type="button" title="新建 Skill" :disabled="skillEditorLoading" @click.stop="openCreateSkill">
            <Plus />
          </button>
          <button type="button" title="导入 Skill" :disabled="skillEditorLoading" @click.stop="openImportSkill">
            <Upload />
          </button>
          <button type="button" title="安装 GitHub Skill" :disabled="skillEditorLoading" @click.stop="openInstallSkill">
            <Download />
          </button>
        </div>
      </div>
      <div v-if="skillsLoading" class="composer-skill-empty">正在加载</div>
      <div v-else-if="installedPreviewSkills.length" class="skill-hub-installed-strip">
        <button
          v-for="skill in installedPreviewSkills"
          :key="skill.path"
          class="skill-hub-icon-tile"
          :class="{ selected: isSkillSelected(skill) }"
          type="button"
          :title="skill.name"
          :aria-pressed="isSkillSelected(skill)"
          @click.stop="toggleSkill(skill)"
        >
          <component v-if="skillIconComponent(skill)" :is="skillIconComponent(skill)" />
          <span v-else>{{ skillIconText(skill) }}</span>
        </button>
      </div>
      <p v-else class="composer-skill-empty">暂无 Skills</p>
      <p v-if="hiddenInstalledSkillCount" class="skill-hub-more">
        另外 {{ hiddenInstalledSkillCount }} 项在下方列表中显示
      </p>
    </section>

    <template v-if="!skillsLoading && skillFilterMode !== 'installable'">
      <section v-for="group in skillGroups" :key="group.id" class="skill-hub-section">
        <div class="skill-hub-section-head">
          <h3>{{ group.title }}</h3>
        </div>
        <div class="composer-skill-list">
          <div v-for="skill in group.skills" :key="skill.path" class="composer-skill-row">
            <button
              class="composer-skill-option"
              :class="{ selected: isSkillSelected(skill) }"
              type="button"
              :aria-pressed="isSkillSelected(skill)"
              @click.stop="toggleSkill(skill)"
            >
              <span class="composer-skill-avatar">
                <component v-if="skillIconComponent(skill)" :is="skillIconComponent(skill)" />
                <span v-else>{{ skillIconText(skill) }}</span>
              </span>
              <span class="composer-skill-copy">
                <span class="composer-skill-name">
                  <span>{{ skill.name }}</span>
                  <small>{{ formatSkillScope(skill.scope) }}</small>
                </span>
                <small>{{ formatSkillDescription(skill) }}</small>
                <small v-if="formatMissingTools(skill)" class="composer-skill-warning">
                  缺少工具：{{ formatMissingTools(skill) }}
                </small>
              </span>
            </button>
            <div class="composer-skill-row-actions">
              <span v-if="isSkillSelected(skill)" class="composer-skill-selected-mark" title="已选择">
                <Check />
              </span>
              <button
                class="composer-skill-action"
                type="button"
                title="编辑 Skill"
                :disabled="skillEditorLoading"
                @click.stop="openEditSkill(skill)"
              >
                <PencilLine />
              </button>
              <button
                v-if="canReinstallSkill(skill)"
                class="composer-skill-action"
                type="button"
                title="重新安装 Skill"
                :disabled="skillEditorLoading"
                @click.stop="reinstallSkill(skill)"
              >
                <RefreshCw />
              </button>
              <button
                class="composer-skill-action danger"
                type="button"
                title="删除 Skill"
                :disabled="skillEditorLoading"
                @click.stop="deleteSkill(skill)"
              >
                <Trash2 />
              </button>
            </div>
          </div>
        </div>
      </section>
    </template>

    <section
      v-if="skillFilterMode === 'all' || skillFilterMode === 'installable'"
      class="skill-hub-section composer-skill-registry"
    >
      <div class="skill-hub-section-head">
        <h3>可安装</h3>
        <button
          class="composer-skill-refresh"
          type="button"
          title="刷新可安装 Skills"
          :disabled="installableSkillsLoading"
          @click.stop="refreshInstallableSkills(true)"
        >
          <RefreshCw :class="{ spinning: installableSkillsLoading }" />
        </button>
      </div>
      <div class="composer-skill-installable-list">
        <div v-if="installableSkillsLoading" class="composer-skill-empty">正在加载</div>
        <template v-else>
          <div
            v-for="skill in filteredInstallableSkills"
            :key="skill.id"
            class="composer-skill-installable-row"
          >
            <span class="composer-skill-avatar">
              <component v-if="skillIconComponent(skill)" :is="skillIconComponent(skill)" />
              <span v-else>{{ skillIconText(skill) }}</span>
            </span>
            <span class="composer-skill-copy">
              <span class="composer-skill-name">
                <span>{{ skill.name }}</span>
                <small :class="{ warning: skill.update_available }">
                  {{ formatInstallableState(skill) }}
                </small>
              </span>
              <small>{{ skill.description }}</small>
              <small v-if="formatInstallableTags(skill)" class="composer-skill-tags">
                {{ formatInstallableTags(skill) }}
              </small>
              <small v-if="formatMissingTools(skill)" class="composer-skill-warning">
                缺少工具：{{ formatMissingTools(skill) }}
              </small>
            </span>
            <button
              class="composer-skill-pill-action"
              type="button"
              :title="skill.update_available ? '更新 Skill' : '安装 Skill'"
              :disabled="installableSkillActionDisabled(skill)"
              @click.stop="submitInstallableSkill(skill)"
            >
              <RefreshCw v-if="skill.update_available && skill.installed_path" />
              <Download v-else />
              <span>{{ skill.update_available ? '更新' : '安装' }}</span>
            </button>
          </div>
        </template>
        <div
          v-if="!installableSkillsLoading && !filteredInstallableSkills.length"
          class="composer-skill-empty"
        >
          暂无可安装 Skills
        </div>
      </div>
      <div v-if="visibleInstallableSkillErrors.length" class="composer-skill-errors">
        <div v-for="error in visibleInstallableSkillErrors" :key="error.source">
          {{ error.message }}
        </div>
      </div>
    </section>

    <SkillEditor
      v-if="activeEditorMode"
      :mode="activeEditorMode"
      :disabled="disabled"
      :initial-skill="activeEditSkill"
      :skill-editor-loading="skillEditorLoading"
      :skill-editor-skill="skillEditorSkill"
      :skill-editor-content="skillEditorContent"
      :skill-management-error="skillManagementError"
      :skill-resources="skillResources"
      :skill-resources-loading="skillResourcesLoading"
      :skill-resource-loading="skillResourceLoading"
      :skill-resource-path="skillResourcePath"
      :skill-resource-content="skillResourceContent"
      :skill-resource-error="skillResourceError"
      @save-skill="saveSkill"
      @import-skill="importSkill"
      @install-skill="installSkill"
      @refresh-skill-resources="refreshSkillResources"
      @load-skill-resource="loadSkillResource"
      @save-skill-resource="saveSkillResource"
      @delete-skill-resource="deleteSkillResource"
      @close="closeSkillEditor"
    />
    <div v-if="visibleSkillErrors.length" class="composer-skill-errors">
      <div v-for="error in visibleSkillErrors" :key="error.path">
        {{ error.message }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer-skill-menu {
  display: grid;
  gap: 6px;
}

.composer-skill-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 5px 0;
}

.composer-skill-title {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.composer-skill-title svg,
.composer-skill-refresh svg {
  width: 15px;
  height: 15px;
}

.composer-skill-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.composer-skill-refresh {
  width: 28px;
  height: 28px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.composer-skill-refresh:hover {
  background: var(--surface-hover);
}

.composer-skill-refresh:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.composer-skill-refresh .spinning {
  animation: skill-menu-spin 0.9s linear infinite;
}

@keyframes skill-menu-spin {
  to {
    transform: rotate(360deg);
  }
}

.composer-skill-list {
  max-height: 232px;
  display: grid;
  gap: 3px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.composer-skill-registry {
  display: grid;
  gap: 5px;
  border-top: 1px solid var(--border-subtle);
  padding: 7px 4px 0;
}

.composer-skill-registry-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 1px;
}

.composer-skill-registry-head strong {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 800;
}

.composer-skill-installable-list {
  max-height: 178px;
  display: grid;
  gap: 3px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.composer-skill-installable-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px;
  align-items: stretch;
  gap: 3px;
}

.composer-skill-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px 28px 28px;
  align-items: stretch;
  gap: 3px;
}

.composer-skill-option {
  width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: 18px 1fr;
  align-items: start;
  gap: 9px;
  border: 0;
  border-radius: 10px;
  padding: 8px 9px;
  color: var(--text-primary);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.composer-skill-option:hover,
.composer-skill-option.selected {
  background: var(--surface-hover);
}

.composer-skill-action {
  width: 28px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.composer-skill-action:hover {
  background: var(--surface-hover);
}

.composer-skill-action:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.composer-skill-action.danger {
  color: var(--danger-text);
}

.composer-skill-action svg {
  width: 14px;
  height: 14px;
}

.composer-skill-check {
  width: 16px;
  height: 16px;
  display: inline-grid;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  margin-top: 1px;
}

.composer-skill-check span {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  background: transparent;
}

.composer-skill-option.selected .composer-skill-check {
  border-color: var(--accent);
  background: var(--accent-bg);
}

.composer-skill-option.selected .composer-skill-check span {
  background: var(--accent);
}

.composer-skill-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.composer-skill-name {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.composer-skill-name > span {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 800;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-skill-name small {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.composer-skill-name small.warning {
  color: var(--warning-strong);
}

.composer-skill-copy > small,
.composer-skill-empty,
.composer-skill-errors {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
}

.composer-skill-copy > small {
  display: -webkit-box;
  overflow: hidden;
  overflow-wrap: anywhere;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.composer-skill-tags {
  color: var(--text-tertiary) !important;
  -webkit-line-clamp: 1 !important;
}

.composer-skill-warning {
  color: var(--warning-strong) !important;
  -webkit-line-clamp: 1 !important;
}

.composer-skill-empty {
  padding: 9px;
}

.composer-skill-errors {
  display: grid;
  gap: 3px;
  border-top: 1px solid var(--border-subtle);
  padding: 7px 8px 3px;
  color: var(--warning-strong);
}

.skill-hub {
  width: min(900px, 100%);
  margin: 0 auto;
  gap: 22px;
}

.skill-hub-tabs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  justify-self: start;
}

.skill-hub-tabs button {
  height: 32px;
  border: 0;
  border-radius: 11px;
  padding: 0 11px;
  color: var(--text-secondary);
  background: transparent;
  font-size: 14px;
  font-weight: 750;
  cursor: pointer;
}

.skill-hub-tabs button.active {
  color: var(--text-primary);
  background: var(--surface-selected-strong);
}

.skill-hub-tabs button:disabled {
  cursor: default;
  opacity: 0.72;
}

.skill-hub-hero {
  display: grid;
  gap: 12px;
}

.skill-hub-hero h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 22px;
  line-height: 1.2;
  font-weight: 750;
}

.skill-hub-hero p {
  margin: -4px 0 2px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.4;
  font-weight: 650;
}

.skill-hub-search-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 40px;
  gap: 8px;
}

.skill-hub-search {
  min-width: 0;
  height: 40px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid var(--border-control);
  border-radius: 14px;
  padding: 0 12px;
  color: var(--text-secondary);
  background: var(--control-bg);
}

.skill-hub-search svg {
  width: 17px;
  height: 17px;
  flex: 0 0 auto;
}

.skill-hub-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  color: var(--text-primary);
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  line-height: 1;
  outline: 0;
}

.skill-hub-search input::placeholder {
  color: var(--text-secondary);
}

.skill-hub-filter {
  width: 40px;
  height: 40px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 999px;
  color: var(--text-secondary);
  background: var(--control-bg);
  cursor: pointer;
}

.skill-hub-filter:hover {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.skill-hub-filter:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.skill-hub-filter svg {
  width: 17px;
  height: 17px;
}

.skill-hub-filter-chips {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
}

.skill-hub-filter-chips button {
  height: 30px;
  flex: 0 0 auto;
  border: 0;
  border-radius: 10px;
  padding: 0 10px;
  color: var(--text-secondary);
  background: transparent;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.skill-hub-filter-chips button:hover,
.skill-hub-filter-chips button.active {
  color: var(--text-primary);
  background: var(--surface-selected-strong);
}

.skill-hub-section {
  min-width: 0;
  display: grid;
  gap: 12px;
}

.skill-hub-section-head {
  min-width: 0;
  min-height: 30px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--border-muted);
  padding: 0 0 9px;
}

.skill-hub-section-head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 15px;
  line-height: 1.2;
  font-weight: 760;
}

.skill-hub-selected-count {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 650;
}

.skill-hub-manage-actions {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.skill-hub-manage-actions button,
.skill-hub .composer-skill-refresh {
  width: 30px;
  height: 30px;
  display: inline-grid;
  place-items: center;
  border: 0;
  border-radius: 10px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.skill-hub-manage-actions button:hover,
.skill-hub .composer-skill-refresh:hover {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.skill-hub-manage-actions button:disabled,
.skill-hub .composer-skill-refresh:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.skill-hub-installed-strip {
  display: flex;
  align-items: center;
  gap: 9px;
  overflow-x: auto;
  padding: 4px 0 2px;
}

.skill-hub-icon-tile {
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  display: inline-grid;
  place-items: center;
  border: 1px solid var(--border-muted);
  border-radius: 12px;
  color: var(--text-primary);
  background: var(--surface-subtle);
  cursor: pointer;
}

.skill-hub-icon-tile:hover,
.skill-hub-icon-tile.selected {
  border-color: var(--accent-border-soft);
  background: var(--accent-bg);
}

.skill-hub-icon-tile span {
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.skill-hub-icon-tile svg {
  width: 19px;
  height: 19px;
}

.skill-hub-more {
  margin: -2px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.skill-hub .composer-skill-list,
.skill-hub .composer-skill-installable-list {
  max-height: none;
  display: grid;
  gap: 10px;
  overflow: visible;
}

.skill-hub .composer-skill-row {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}

.skill-hub .composer-skill-option {
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  border-radius: 10px;
  padding: 0;
  background: transparent;
}

.skill-hub .composer-skill-option:hover,
.skill-hub .composer-skill-option.selected {
  background: transparent;
}

.composer-skill-avatar {
  width: 40px;
  height: 40px;
  display: inline-grid;
  place-items: center;
  border: 1px solid var(--border-muted);
  border-radius: 12px;
  color: var(--text-primary);
  background: var(--surface-subtle);
}

.composer-skill-avatar svg {
  width: 19px;
  height: 19px;
}

.composer-skill-avatar span {
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.skill-hub .composer-skill-copy {
  gap: 3px;
}

.skill-hub .composer-skill-name {
  gap: 8px;
}

.skill-hub .composer-skill-name > span {
  font-size: 14px;
  line-height: 1.25;
}

.skill-hub .composer-skill-name small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.skill-hub .composer-skill-copy > small,
.skill-hub .composer-skill-empty,
.skill-hub .composer-skill-errors {
  font-size: 12px;
  line-height: 1.35;
}

.composer-skill-row-actions {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.composer-skill-selected-mark {
  width: 30px;
  height: 30px;
  display: inline-grid;
  place-items: center;
  color: var(--text-secondary);
}

.composer-skill-selected-mark svg {
  width: 17px;
  height: 17px;
}

.skill-hub .composer-skill-action {
  width: 30px;
  height: 30px;
  border-radius: 10px;
}

.skill-hub .composer-skill-action svg {
  width: 14px;
  height: 14px;
}

.skill-hub .composer-skill-registry {
  border-top: 0;
  padding: 0;
}

.skill-hub .composer-skill-installable-row {
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.composer-skill-pill-action {
  min-width: 72px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  padding: 0 11px;
  color: var(--text-primary);
  background: transparent;
  font-size: 12px;
  font-weight: 750;
  cursor: pointer;
}

.composer-skill-pill-action:hover:not(:disabled) {
  background: var(--surface-hover);
}

.composer-skill-pill-action:disabled {
  color: var(--text-disabled);
  cursor: default;
}

.composer-skill-pill-action svg {
  width: 13px;
  height: 13px;
}

.skill-hub .composer-skill-errors {
  border-top: 0;
  padding: 0;
}

@media (max-width: 760px) {
  .skill-hub {
    gap: 20px;
  }

  .skill-hub-hero h2 {
    font-size: 21px;
  }

  .skill-hub-hero p {
    font-size: 13px;
  }

  .skill-hub-search-row {
    grid-template-columns: minmax(0, 1fr) 40px;
  }

  .skill-hub-search,
  .skill-hub-filter {
    height: 40px;
  }

  .skill-hub .composer-skill-row,
  .skill-hub .composer-skill-installable-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .composer-skill-row-actions {
    justify-content: flex-start;
    padding-left: 52px;
  }
}
</style>
