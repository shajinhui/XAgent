<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import {
  Blocks,
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ExternalLink,
  Folder,
  FolderPlus,
  Info,
  MessageSquarePlus,
  Palette,
  PanelLeft,
  Plus,
  Search,
  Settings as SettingsIcon,
  Shield,
  Trash2
} from '@lucide/vue'
import ChatComposer from '@renderer/components/ChatComposer.vue'
import ClarificationDialog from '@renderer/components/ClarificationDialog.vue'
import MessageList from '@renderer/components/MessageList.vue'
import PermissionDialog from '@renderer/components/PermissionDialog.vue'
import TitleBar from '@renderer/components/TitleBar.vue'
import { useChatStore } from '@renderer/stores/chat'
import { useRuntimeStore } from '@renderer/stores/runtime'
import { useThemeStore } from '@renderer/stores/theme'
import type { RuntimeSessionSummary, RuntimeWorkspaceProject } from '@renderer/types/runtimeEvents'
import type { ThemeMode } from '../../preload'

type ConversationSessionItem = RuntimeSessionSummary & {
  selectedRoot: string
}

type MainView = 'chat' | 'settings'
type SettingsSection = 'appearance' | 'model' | 'permissions' | 'workspace' | 'about'
type SettingsSectionItem = {
  id: SettingsSection
  label: string
  icon: Component
}

type SessionContextMenu = {
  session: RuntimeSessionSummary
  selectedRoot: string
  x: number
  y: number
}

const chat = useChatStore()
const runtime = useRuntimeStore()
const theme = useThemeStore()
const sidebarOpen = ref(true)
const sidebarWidth = ref(286)
const projectsExpanded = ref(true)
const collapsedProjectRoots = ref<Set<string>>(new Set())
const isResizingSidebar = ref(false)
const sessionContextMenu = ref<SessionContextMenu | null>(null)
const mainView = ref<MainView>('chat')
const settingsSection = ref<SettingsSection>('appearance')
const SIDEBAR_MIN_WIDTH = 248
const SIDEBAR_MAX_WIDTH = 360
const SIDEBAR_WIDTH_STORAGE_KEY = 'codex-mini.sidebar-width'
const settingSections: SettingsSectionItem[] = [
  { id: 'appearance', label: '外观', icon: Palette },
  { id: 'model', label: '模型', icon: Bot },
  { id: 'permissions', label: '权限', icon: Shield },
  { id: 'workspace', label: '工作区', icon: Folder },
  { id: 'about', label: '关于', icon: Info }
]
const themeOptions: Array<{ mode: ThemeMode; label: string }> = [
  { mode: 'system', label: '跟随系统' },
  { mode: 'light', label: '浅色' },
  { mode: 'dark', label: '深色' }
]

const visibleProjects = computed(() => runtime.workspaceProjects)
const isSettingsView = computed(() => mainView.value === 'settings')
const hasConversationStarted = computed(() =>
  chat.messages.some((message) => message.role === 'user')
)
const titleBarTitle = computed(() => (isSettingsView.value ? '设置' : chat.conversationTitle))
const titleBarMessageCount = computed(() => (isSettingsView.value ? 0 : chat.messageCount))
const themeStatus = computed(() => {
  if (theme.mode !== 'system') return theme.label
  return `跟随系统 · ${theme.resolved === 'dark' ? '深色' : '浅色'}`
})
const conversationSessions = computed<ConversationSessionItem[]>(() => {
  const candidates = runtime.conversationSelectedRoots.flatMap((root) => {
    const workspaceSessions =
      runtime.workspace?.selected_root === root && runtime.sessionHistory.length
        ? runtime.sessionHistory
        : runtime.sessionsBySelectedRoot[root] || []

    return workspaceSessions.map((session) => ({
      ...session,
      selectedRoot: root
    }))
  })

  const seen = new Set<string>()
  return candidates
    .sort((left, right) => right.updated_at - left.updated_at)
    .filter((session) => {
      if (seen.has(session.session_id)) return false
      seen.add(session.session_id)
      return true
    })
})

const composerPlaceholder = computed(() => {
  if (runtime.isSuspended) return '会话已挂起，请先恢复...'
  if (runtime.isConnecting) return '正在连接后端...'
  return '输入消息...'
})

const composerDisabled = computed(
  () => runtime.isConnecting || runtime.isSuspended || Boolean(runtime.activeTurnId)
)
function toggleSidebar(): void {
  sidebarOpen.value = !sidebarOpen.value
}

function toggleProjectsExpanded(): void {
  projectsExpanded.value = !projectsExpanded.value
}

function isProjectExpanded(root: string): boolean {
  return !collapsedProjectRoots.value.has(root)
}

function toggleProjectConversation(root: string): void {
  const next = new Set(collapsedProjectRoots.value)
  if (next.has(root)) {
    next.delete(root)
  } else {
    next.add(root)
  }
  collapsedProjectRoots.value = next
}

function sessionsForProject(root: string): RuntimeSessionSummary[] {
  if (runtime.workspace?.selected_root === root) {
    const cachedSessions = runtime.sessionsBySelectedRoot[root] || []
    return cachedSessions.length ? cachedSessions : runtime.sessionHistory
  }
  return runtime.sessionsBySelectedRoot[root] || []
}

async function openProject(project: RuntimeWorkspaceProject): Promise<void> {
  mainView.value = 'chat'
  if (runtime.workspace?.selected_root === project.selected_root) return
  await runtime.openWorkspace(project.selected_root)
}

async function startProjectConversation(project: RuntimeWorkspaceProject): Promise<void> {
  mainView.value = 'chat'
  await runtime.startNewConversationInWorkspace(project.selected_root)
}

async function resumeConversation(root: string, sessionId: string): Promise<void> {
  mainView.value = 'chat'
  await runtime.resumeSessionInWorkspace(root, sessionId)
}

function clampSidebarWidth(value: number): number {
  const viewportLimit = Math.max(SIDEBAR_MIN_WIDTH, window.innerWidth - 560)
  return Math.round(Math.min(Math.max(value, SIDEBAR_MIN_WIDTH), SIDEBAR_MAX_WIDTH, viewportLimit))
}

function saveSidebarWidth(): void {
  window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth.value))
}

function syncSidebarWidth(): void {
  const nextWidth = clampSidebarWidth(sidebarWidth.value)
  if (nextWidth === sidebarWidth.value) return

  sidebarWidth.value = nextWidth
  saveSidebarWidth()
}

function resizeSidebar(event: PointerEvent): void {
  if (!isResizingSidebar.value) return

  sidebarWidth.value = clampSidebarWidth(event.clientX)
}

function stopSidebarResize(): void {
  if (!isResizingSidebar.value) return

  isResizingSidebar.value = false
  saveSidebarWidth()
  window.removeEventListener('pointermove', resizeSidebar)
  window.removeEventListener('pointerup', stopSidebarResize)
}

function startSidebarResize(event: PointerEvent): void {
  event.preventDefault()
  isResizingSidebar.value = true
  closeSessionContextMenu()
  window.addEventListener('pointermove', resizeSidebar)
  window.addEventListener('pointerup', stopSidebarResize)
}

function openSessionContextMenu(
  event: MouseEvent,
  session: RuntimeSessionSummary,
  selectedRoot: string
): void {
  event.preventDefault()
  event.stopPropagation()

  sessionContextMenu.value = {
    session,
    selectedRoot,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - 168)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - 74))
  }
}

function closeSessionContextMenu(): void {
  sessionContextMenu.value = null
}

function openSettings(section: SettingsSection = 'appearance'): void {
  closeSessionContextMenu()
  settingsSection.value = section
  mainView.value = 'settings'
}

async function deleteContextSession(): Promise<void> {
  const target = sessionContextMenu.value
  if (!target) return

  closeSessionContextMenu()
  const confirmed = window.confirm(`删除会话“${target.session.title}”？此操作会删除本地记录。`)
  if (!confirmed) return

  await runtime.deleteSessionInWorkspace(target.selectedRoot, target.session.session_id)
}

function handleGlobalKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeSessionContextMenu()
  }
}

async function openWorkspaceFromDialog(): Promise<void> {
  try {
    const selectedPath = await window.api.openWorkspaceDirectory(runtime.workspace?.selected_root)
    if (!selectedPath) return
    await runtime.openWorkspace(selectedPath)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`打开工作区失败：${message}`)
  }
}

async function changeDirectoryFromDialog(): Promise<void> {
  if (!runtime.workspace) return

  try {
    const selectedPath = await window.api.openWorkspaceDirectory(runtime.workspace.current_dir)
    if (!selectedPath) return
    await runtime.changeDirectory(selectedPath)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`切换当前目录失败：${message}`)
  }
}

async function addWorkspaceDirectoryFromDialog(): Promise<void> {
  if (!runtime.workspace) return

  try {
    const selectedPath = await window.api.openWorkspaceDirectory(runtime.workspace.selected_root)
    if (!selectedPath) return
    const access = window.confirm('允许这个额外目录写入吗？') ? 'write' : 'read'
    await runtime.addWorkspaceDirectory(selectedPath, access)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`加入额外目录失败：${message}`)
  }
}

async function trustWorkspace(): Promise<void> {
  if (!runtime.workspace) return
  const confirmed = window.confirm(
    '信任当前项目后，后端会读取 .codex-mini/config.toml 中白名单允许的策略配置。继续吗？'
  )
  if (!confirmed) return

  try {
    await runtime.trustWorkspace()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`信任工作区失败：${message}`)
  }
}

async function untrustWorkspace(): Promise<void> {
  if (!runtime.workspace) return

  try {
    await runtime.untrustWorkspace()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`取消信任工作区失败：${message}`)
  }
}

async function createDefaultConversationWorkspace(): Promise<void> {
  try {
    mainView.value = 'chat'
    const workspacePath = await window.api.createDefaultChatDirectory()
    await runtime.openConversationWorkspace(workspacePath)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`创建普通对话目录失败：${message}`)
  }
}

function formatSessionUpdatedAt(timestamp: number): string {
  const date = new Date(timestamp * 1000)
  const diffSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000))
  const diffMinutes = Math.floor(diffSeconds / 60)
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)
  const diffWeeks = Math.floor(diffDays / 7)

  if (diffMinutes < 1) return '刚刚'
  if (diffHours < 1) return `${diffMinutes} 分钟`
  if (diffDays < 1) return `${diffHours} 小时`
  if (diffWeeks < 1) return `${diffDays} 天`
  return `${diffWeeks} 周`
}

onMounted(() => {
  const savedWidth = Number(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY))
  if (Number.isFinite(savedWidth)) {
    sidebarWidth.value = clampSidebarWidth(savedWidth)
  }

  window.addEventListener('resize', syncSidebarWidth)
  window.addEventListener('click', closeSessionContextMenu)
  window.addEventListener('keydown', handleGlobalKeydown)
  void runtime.connect()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncSidebarWidth)
  window.removeEventListener('click', closeSessionContextMenu)
  window.removeEventListener('keydown', handleGlobalKeydown)
  window.removeEventListener('pointermove', resizeSidebar)
  window.removeEventListener('pointerup', stopSidebarResize)
})
</script>

<template>
  <main
    class="app-screen"
    :class="{ 'sidebar-open': sidebarOpen, 'sidebar-resizing': isResizingSidebar }"
    :style="{ '--sidebar-width': `${sidebarWidth}px` }"
  >
    <aside class="app-sidebar" :aria-hidden="!sidebarOpen">
      <div class="sidebar-top">
        <span class="sidebar-window-spacer" aria-hidden="true"></span>
        <button
          class="sidebar-toggle in-sidebar"
          type="button"
          :aria-label="sidebarOpen ? '收起侧边栏' : '打开侧边栏'"
          @click="toggleSidebar"
        >
          <PanelLeft />
        </button>
        <button class="sidebar-icon-button" type="button" aria-label="后退" disabled>
          <ChevronLeft />
        </button>
        <button class="sidebar-icon-button" type="button" aria-label="前进" disabled>
          <ChevronRight />
        </button>
      </div>

      <nav class="sidebar-primary" aria-label="侧边栏操作">
        <button
          type="button"
          class="sidebar-action active"
          @click="void createDefaultConversationWorkspace()"
        >
          <MessageSquarePlus />
          <span>新对话</span>
        </button>
        <button type="button" class="sidebar-action">
          <Search />
          <span>搜索</span>
        </button>
        <button type="button" class="sidebar-action">
          <Blocks />
          <span>插件</span>
        </button>
        <button type="button" class="sidebar-action">
          <Clock3 />
          <span>自动化</span>
        </button>
      </nav>

      <section class="sidebar-content" aria-label="工作区与会话">
        <div class="sidebar-section">
          <div class="sidebar-section-heading">
            <button
              type="button"
              class="sidebar-heading-main"
              :class="{ collapsed: !projectsExpanded }"
              :aria-expanded="projectsExpanded"
              @click="toggleProjectsExpanded"
            >
              <span>项目</span>
              <ChevronDown />
            </button>
            <div class="sidebar-heading-actions" aria-label="项目操作">
              <button type="button" aria-label="打开工作区" @click="openWorkspaceFromDialog">
                <FolderPlus />
              </button>
            </div>
          </div>

          <template v-if="projectsExpanded">
            <div
              v-for="project in visibleProjects"
              :key="project.selected_root"
              class="sidebar-project-block"
            >
              <div class="sidebar-project-row">
                <button
                  type="button"
                  class="sidebar-project"
                  :class="{
                    collapsed: !isProjectExpanded(project.selected_root),
                    active: runtime.workspace?.selected_root === project.selected_root
                  }"
                  :title="project.selected_root"
                  :aria-expanded="isProjectExpanded(project.selected_root)"
                  @click="toggleProjectConversation(project.selected_root)"
                >
                  <Folder />
                  <span>{{ project.display_name }}</span>
                  <ChevronDown class="sidebar-project-chevron" />
                </button>
                <button
                  type="button"
                  class="sidebar-project-action"
                  aria-label="切换到项目"
                  title="切换到项目"
                  @click="openProject(project)"
                >
                  <ExternalLink />
                </button>
                <button
                  type="button"
                  class="sidebar-project-action"
                  aria-label="在此项目中新建会话"
                  title="在此项目中新建会话"
                  @click="startProjectConversation(project)"
                >
                  <Plus />
                </button>
              </div>

              <div v-if="isProjectExpanded(project.selected_root)" class="sidebar-session-group">
                <button
                  v-for="session in sessionsForProject(project.selected_root)"
                  :key="session.session_id"
                  type="button"
                  class="sidebar-item"
                  :class="{ selected: session.session_id === runtime.selectedSessionId }"
                  :title="session.last_message || session.title"
                  @click="void resumeConversation(project.selected_root, session.session_id)"
                  @contextmenu="openSessionContextMenu($event, session, project.selected_root)"
                >
                  <span>{{ session.title }}</span>
                  <small>{{ formatSessionUpdatedAt(session.updated_at) }}</small>
                </button>
                <p
                  v-if="
                    runtime.sessionsLoading &&
                    runtime.workspace?.selected_root === project.selected_root
                  "
                  class="sidebar-empty"
                >
                  正在加载
                </p>
                <p
                  v-else-if="!sessionsForProject(project.selected_root).length"
                  class="sidebar-empty"
                >
                  暂无历史会话
                </p>
              </div>
            </div>
            <p v-if="!visibleProjects.length" class="sidebar-empty">暂无项目</p>
          </template>
        </div>

        <div class="sidebar-section conversation-section">
          <div class="sidebar-section-heading">
            <button type="button" class="sidebar-heading-main">
              <span>对话</span>
            </button>
            <div class="sidebar-heading-actions" aria-label="对话操作">
              <button
                type="button"
                aria-label="新建普通对话"
                title="创建普通对话目录"
                @click="void createDefaultConversationWorkspace()"
              >
                <Plus />
              </button>
            </div>
          </div>
          <button
            v-for="session in conversationSessions"
            :key="session.session_id"
            type="button"
            class="sidebar-item conversation-item"
            :class="{ selected: session.session_id === runtime.selectedSessionId }"
            :title="session.last_message || session.title"
            @click="void resumeConversation(session.selectedRoot, session.session_id)"
            @contextmenu="openSessionContextMenu($event, session, session.selectedRoot)"
          >
            <span>{{ session.title }}</span>
            <small>{{ formatSessionUpdatedAt(session.updated_at) }}</small>
          </button>
          <p v-if="runtime.sessionsLoading && !conversationSessions.length" class="sidebar-empty">
            正在加载
          </p>
          <p v-else-if="!conversationSessions.length" class="sidebar-empty">暂无历史会话</p>
        </div>
      </section>

      <button
        class="sidebar-account"
        :class="{ active: isSettingsView }"
        type="button"
        @click="openSettings()"
      >
        <SettingsIcon />
        <span>设置</span>
      </button>

      <div
        class="sidebar-resizer"
        role="separator"
        aria-label="调整侧边栏宽度"
        @pointerdown="startSidebarResize"
      ></div>
    </aside>

    <div v-if="sidebarOpen" class="sidebar-scrim" @click="toggleSidebar"></div>

    <div
      v-if="sessionContextMenu"
      class="session-context-menu"
      :style="{ left: `${sessionContextMenu.x}px`, top: `${sessionContextMenu.y}px` }"
      role="menu"
      @click.stop
      @contextmenu.prevent
    >
      <button type="button" class="danger" role="menuitem" @click="void deleteContextSession()">
        <Trash2 />
        <span>删除会话</span>
      </button>
    </div>

    <section
      class="chat-window"
      :class="{
        'conversation-empty': mainView === 'chat' && !hasConversationStarted,
        'settings-active': isSettingsView
      }"
      aria-label="Codex-mini chat preview"
    >
      <TitleBar
        :title="titleBarTitle"
        :title-status="isSettingsView ? 'ready' : chat.conversationTitleStatus"
        :message-count="titleBarMessageCount"
        :connection-status="runtime.connectionStatus"
        :is-suspended="runtime.isSuspended"
        :sidebar-open="sidebarOpen"
        :workspace="runtime.workspace"
        @toggle-sidebar="toggleSidebar"
        @connect="runtime.connect"
        @disconnect="runtime.disconnect"
        @resume="runtime.resumeSession"
        @new-conversation="createDefaultConversationWorkspace"
        @open-workspace="openWorkspaceFromDialog"
        @change-directory="changeDirectoryFromDialog"
        @add-directory="addWorkspaceDirectoryFromDialog"
        @trust-workspace="trustWorkspace"
        @untrust-workspace="untrustWorkspace"
      />
      <div v-if="isSettingsView" class="settings-page">
        <nav class="settings-menu" aria-label="设置菜单">
          <button
            v-for="section in settingSections"
            :key="section.id"
            type="button"
            class="settings-menu-item"
            :class="{ active: settingsSection === section.id }"
            @click="settingsSection = section.id"
          >
            <component :is="section.icon" />
            <span>{{ section.label }}</span>
          </button>
        </nav>

        <section class="settings-content" aria-label="设置内容">
          <template v-if="settingsSection === 'appearance'">
            <header class="settings-content-header">
              <h2>外观</h2>
              <p>{{ themeStatus }}</p>
            </header>
            <div class="settings-card">
              <div class="settings-row">
                <div class="settings-row-copy">
                  <strong>主题</strong>
                  <span>深色模式保持当前外观</span>
                </div>
                <div class="settings-segmented" role="group" aria-label="主题">
                  <button
                    v-for="option in themeOptions"
                    :key="option.mode"
                    type="button"
                    :class="{ active: theme.mode === option.mode }"
                    @click="void theme.setMode(option.mode)"
                  >
                    {{ option.label }}
                  </button>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="settingsSection === 'model'">
            <header class="settings-content-header">
              <h2>模型</h2>
              <p>{{ runtime.selectedModel }}</p>
            </header>
            <div class="settings-card">
              <div class="settings-row">
                <div class="settings-row-copy">
                  <strong>思考程度</strong>
                  <span>{{ runtime.reasoningEffort }}</span>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="settingsSection === 'permissions'">
            <header class="settings-content-header">
              <h2>权限</h2>
              <p>{{ runtime.permissionMode }}</p>
            </header>
            <div class="settings-card">
              <div class="settings-row">
                <div class="settings-row-copy">
                  <strong>当前模式</strong>
                  <span>{{ runtime.permissionMode }}</span>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="settingsSection === 'workspace'">
            <header class="settings-content-header">
              <h2>工作区</h2>
              <p>{{ runtime.workspace?.display_name || '未打开工作区' }}</p>
            </header>
            <div class="settings-card">
              <div class="settings-row">
                <div class="settings-row-copy">
                  <strong>当前目录</strong>
                  <span>{{ runtime.workspace?.current_dir || '-' }}</span>
                </div>
              </div>
            </div>
          </template>

          <template v-else>
            <header class="settings-content-header">
              <h2>关于</h2>
              <p>Codex-mini</p>
            </header>
            <div class="settings-card">
              <div class="settings-row">
                <div class="settings-row-copy">
                  <strong>桌面端</strong>
                  <span>Electron + Vue</span>
                </div>
              </div>
            </div>
          </template>
        </section>
      </div>
      <template v-else>
        <MessageList :messages="chat.messages" />
        <div class="composer-zone">
          <PermissionDialog
            v-if="runtime.activePermission"
            :request="runtime.activePermission"
            @approve="runtime.approvePermission"
            @deny="runtime.denyPermission"
          />
          <ClarificationDialog
            v-else-if="runtime.activeClarification"
            :request="runtime.activeClarification"
            @answer="runtime.answerClarification"
            @skip="runtime.skipClarification"
          />
          <ChatComposer
            v-else
            :disabled="composerDisabled"
            :placeholder="composerPlaceholder"
            :model="runtime.selectedModel"
            :model-options="runtime.modelOptions"
            :reasoning-effort="runtime.reasoningEffort"
            :reasoning-options="runtime.reasoningEffortOptions"
            :permission-mode="runtime.permissionMode"
            @send="runtime.sendUserInput"
            @update:model="runtime.setSelectedModel"
            @update:reasoning-effort="runtime.setReasoningEffort"
            @update:permission-mode="runtime.setPermissionMode"
          />
        </div>
      </template>
    </section>
  </main>
</template>
