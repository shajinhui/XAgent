<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatComposer from '@renderer/components/ChatComposer.vue'
import MessageList from '@renderer/components/MessageList.vue'
import PermissionDialog from '@renderer/components/PermissionDialog.vue'
import TitleBar from '@renderer/components/TitleBar.vue'
import { useChatStore } from '@renderer/stores/chat'
import { useRuntimeStore } from '@renderer/stores/runtime'
import type { RuntimeSessionSummary, RuntimeWorkspaceProject } from '@renderer/types/runtimeEvents'

type ConversationSessionItem = RuntimeSessionSummary & {
  workspaceRoot: string
}

type SessionContextMenu = {
  session: RuntimeSessionSummary
  workspaceRoot: string
  x: number
  y: number
}

const chat = useChatStore()
const runtime = useRuntimeStore()
const sidebarOpen = ref(true)
const sidebarWidth = ref(286)
const projectsExpanded = ref(true)
const collapsedProjectRoots = ref<Set<string>>(new Set())
const isResizingSidebar = ref(false)
const sessionContextMenu = ref<SessionContextMenu | null>(null)
const SIDEBAR_MIN_WIDTH = 248
const SIDEBAR_MAX_WIDTH = 360
const SIDEBAR_WIDTH_STORAGE_KEY = 'codex-mini.sidebar-width'

const visibleProjects = computed(() => runtime.workspaceProjects)
const conversationSessions = computed<ConversationSessionItem[]>(() => {
  const sessions = runtime.conversationWorkspaceRoots.flatMap((root) => {
    const workspaceSessions =
      runtime.workspace?.root === root && runtime.sessionHistory.length
        ? runtime.sessionHistory
        : runtime.sessionsByWorkspaceRoot[root] || []

    return workspaceSessions.map((session) => ({
      ...session,
      workspaceRoot: root
    }))
  })

  return sessions.sort((left, right) => right.updated_at - left.updated_at)
})

const composerPlaceholder = computed(() => {
  if (runtime.isSuspended) return '会话已挂起，请先恢复...'
  if (runtime.isConnecting) return '正在连接后端...'
  return '输入消息...'
})

const composerDisabled = computed(() => runtime.isConnecting || runtime.isSuspended)
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
  if (runtime.workspace?.root === root) {
    const cachedSessions = runtime.sessionsByWorkspaceRoot[root] || []
    return cachedSessions.length ? cachedSessions : runtime.sessionHistory
  }
  return runtime.sessionsByWorkspaceRoot[root] || []
}

async function openProject(project: RuntimeWorkspaceProject): Promise<void> {
  if (runtime.workspace?.root === project.root) return
  await runtime.openWorkspace(project.root)
}

async function startProjectConversation(project: RuntimeWorkspaceProject): Promise<void> {
  await runtime.startNewConversationInWorkspace(project.root)
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
  workspaceRoot: string
): void {
  event.preventDefault()
  event.stopPropagation()

  sessionContextMenu.value = {
    session,
    workspaceRoot,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - 168)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - 74))
  }
}

function closeSessionContextMenu(): void {
  sessionContextMenu.value = null
}

async function deleteContextSession(): Promise<void> {
  const target = sessionContextMenu.value
  if (!target) return

  closeSessionContextMenu()
  const confirmed = window.confirm(`删除会话“${target.session.title}”？此操作会删除本地记录。`)
  if (!confirmed) return

  await runtime.deleteSessionInWorkspace(target.workspaceRoot, target.session.session_id)
}

function handleGlobalKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeSessionContextMenu()
  }
}

async function openWorkspaceFromDialog(): Promise<void> {
  try {
    const selectedPath = await window.api.openWorkspaceDirectory(runtime.workspace?.root)
    if (!selectedPath) return
    await runtime.openWorkspace(selectedPath)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    chat.addSystemMessage(`打开工作区失败：${message}`)
  }
}

async function createDefaultConversationWorkspace(): Promise<void> {
  try {
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
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 5h16v14H4V5Zm6 0v14" />
          </svg>
        </button>
        <button class="sidebar-icon-button" type="button" aria-label="后退" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </button>
        <button class="sidebar-icon-button" type="button" aria-label="前进" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m9 6 6 6-6 6" />
          </svg>
        </button>
      </div>

      <nav class="sidebar-primary" aria-label="侧边栏操作">
        <button
          type="button"
          class="sidebar-action active"
          @click="void createDefaultConversationWorkspace()"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5h7v7M18 13.5V19H5V6h5.5" />
            <path d="m13 11 6-6" />
          </svg>
          <span>新对话</span>
        </button>
        <button type="button" class="sidebar-action">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m21 21-4.4-4.4M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z" />
          </svg>
          <span>搜索</span>
        </button>
        <button type="button" class="sidebar-action">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 7h4V3H4v4Zm12 0h4V3h-4v4ZM4 21h4v-4H4v4Zm12 0h4v-4h-4v4Z" />
          </svg>
          <span>插件</span>
        </button>
        <button type="button" class="sidebar-action">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 7v5l3 2" />
            <path d="M21 12a9 9 0 1 1-9-9" />
          </svg>
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
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m7 10 5 5 5-5" />
              </svg>
            </button>
            <div class="sidebar-heading-actions" aria-label="项目操作">
              <button type="button" aria-label="打开工作区" @click="openWorkspaceFromDialog">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 19V5h7l2 2h7v12H4Z" />
                  <path d="M12 11v5M9.5 13.5h5" />
                </svg>
              </button>
            </div>
          </div>

          <template v-if="projectsExpanded">
            <div
              v-for="project in visibleProjects"
              :key="project.root"
              class="sidebar-project-block"
            >
              <div class="sidebar-project-row">
                <button
                  type="button"
                  class="sidebar-project"
                  :class="{
                    collapsed: !isProjectExpanded(project.root),
                    active: runtime.workspace?.root === project.root
                  }"
                  :title="project.root"
                  :aria-expanded="isProjectExpanded(project.root)"
                  @click="toggleProjectConversation(project.root)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M3.5 6.5h6l2 2h9v10h-17v-12Z" />
                  </svg>
                  <span>{{ project.display_name }}</span>
                  <svg class="sidebar-project-chevron" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="m7 10 5 5 5-5" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="sidebar-project-action"
                  aria-label="切换到项目"
                  title="切换到项目"
                  @click="openProject(project)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M7 17 17 7M9 7h8v8" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="sidebar-project-action"
                  aria-label="在此项目中新建会话"
                  title="在此项目中新建会话"
                  @click="startProjectConversation(project)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                </button>
              </div>

              <div v-if="isProjectExpanded(project.root)" class="sidebar-session-group">
                <button
                  v-for="session in sessionsForProject(project.root)"
                  :key="session.session_id"
                  type="button"
                  class="sidebar-item"
                  :class="{ selected: session.session_id === runtime.selectedSessionId }"
                  :title="session.last_message || session.title"
                  @click="void runtime.resumeSessionInWorkspace(project.root, session.session_id)"
                  @contextmenu="openSessionContextMenu($event, session, project.root)"
                >
                  <span>{{ session.title }}</span>
                  <small>{{ formatSessionUpdatedAt(session.updated_at) }}</small>
                </button>
                <p
                  v-if="runtime.sessionsLoading && runtime.workspace?.root === project.root"
                  class="sidebar-empty"
                >
                  正在加载
                </p>
                <p v-else-if="!sessionsForProject(project.root).length" class="sidebar-empty">
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
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
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
            @click="
              void runtime.resumeSessionInWorkspace(session.workspaceRoot, session.session_id)
            "
            @contextmenu="openSessionContextMenu($event, session, session.workspaceRoot)"
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

      <div class="sidebar-account">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z" />
          <path
            d="M4 12a8 8 0 0 1 .2-1.8l-1.7-1 2-3.5 1.8.7A8 8 0 0 1 8 5.4L8.3 3h4l.4 2.4a8 8 0 0 1 1.7 1l1.8-.7 2 3.5-1.7 1A8 8 0 0 1 16.7 12a8 8 0 0 1-.2 1.8l1.7 1-2 3.5-1.8-.7a8 8 0 0 1-1.7 1l-.4 2.4h-4L8 18.6a8 8 0 0 1-1.7-1l-1.8.7-2-3.5 1.7-1A8 8 0 0 1 4 12Z"
          />
        </svg>
        <span>设置</span>
      </div>

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
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7h16M10 11v6M14 11v6M9 7l1-2h4l1 2M6 7l1 14h10l1-14" />
        </svg>
        <span>删除会话</span>
      </button>
    </div>

    <section class="chat-window" aria-label="Codex-mini chat preview">
      <TitleBar
        :title="chat.conversationTitle"
        :title-status="chat.conversationTitleStatus"
        :message-count="chat.messageCount"
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
      />
      <MessageList :messages="chat.messages" />
      <div class="composer-zone">
        <PermissionDialog
          v-if="runtime.activePermission"
          :request="runtime.activePermission"
          @approve="runtime.approvePermission"
          @deny="runtime.denyPermission"
        />
        <ChatComposer
          v-else
          :disabled="composerDisabled"
          :placeholder="composerPlaceholder"
          :model="runtime.selectedModel"
          :model-options="runtime.modelOptions"
          :reasoning-effort="runtime.reasoningEffort"
          :reasoning-options="runtime.reasoningEffortOptions"
          @send="runtime.sendUserInput"
          @update:model="runtime.setSelectedModel"
          @update:reasoning-effort="runtime.setReasoningEffort"
        />
      </div>
    </section>
  </main>
</template>
