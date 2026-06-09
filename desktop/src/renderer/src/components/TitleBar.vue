<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeConnectionStatus } from '@renderer/services/runtimeSocket'
import type { RuntimeWorkspace } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  title: string
  titleStatus: 'idle' | 'pending' | 'ready' | 'error'
  messageCount: number
  connectionStatus: RuntimeConnectionStatus
  isSuspended: boolean
  sidebarOpen: boolean
  workspace: RuntimeWorkspace | null
}>()

const emit = defineEmits<{
  toggleSidebar: []
  connect: []
  disconnect: []
  resume: []
  newConversation: []
  openWorkspace: []
  changeDirectory: []
  addDirectory: []
  trustWorkspace: []
  untrustWorkspace: []
}>()

const statusLabel = computed(() => {
  if (props.isSuspended) return '已挂起'
  if (props.connectionStatus === 'connected') return '已连接'
  if (props.connectionStatus === 'connecting') return '连接中'
  if (props.connectionStatus === 'error') return '连接失败'
  return '离线'
})

const canDisconnect = computed(() => props.connectionStatus === 'connected')

const displayTitle = computed(() => props.title.trim() || '新对话')

const workspaceLabel = computed(() => props.workspace?.display_name || '打开工作区')

const workspaceTrustLabel = computed(() => {
  const level = props.workspace?.trust.level
  if (level === 'trusted') return 'trusted'
  if (level === 'untrusted') return 'untrusted'
  return 'session only'
})

const isWorkspaceTrusted = computed(() => props.workspace?.trust.level === 'trusted')

const trustActionLabel = computed(() =>
  isWorkspaceTrusted.value ? '取消信任当前项目' : '信任当前项目'
)

const currentDirLabel = computed(() => {
  if (!props.workspace) return ''
  const root = trimTrailingSeparators(props.workspace.selected_root)
  const current = trimTrailingSeparators(props.workspace.current_dir)
  if (current === root) return '.'
  if (current.startsWith(`${root}/`)) return current.slice(root.length + 1)
  return current
})

const workspaceTitle = computed(() => {
  if (!props.workspace) return '打开工作区'
  const roots = props.workspace.additional_roots
    .map((root) => `${root.access === 'write' ? 'write' : 'read'} ${root.path}`)
    .join('\n')
  return [
    `selected: ${props.workspace.selected_root}`,
    `current: ${props.workspace.current_dir}`,
    `trust: ${props.workspace.trust.level}`,
    roots ? `additional:\n${roots}` : ''
  ]
    .filter(Boolean)
    .join('\n')
})

function trimTrailingSeparators(path: string): string {
  return path.replace(/[\\/]+$/, '')
}
</script>

<template>
  <header class="titlebar">
    <button
      v-if="!sidebarOpen"
      class="sidebar-toggle floating"
      type="button"
      aria-label="打开侧边栏"
      @click="emit('toggleSidebar')"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 5h16v14H4V5Zm6 0v14" />
      </svg>
    </button>

    <div class="title-copy">
      <span class="app-mark" aria-hidden="true"></span>
      <h1 :title="displayTitle" :class="{ pending: titleStatus === 'pending' }">
        {{ displayTitle }}
      </h1>
      <p>
        <span class="status-dot" :class="connectionStatus"></span>
        {{ statusLabel }} · {{ messageCount }} 条消息
      </p>
    </div>

    <nav class="title-actions" aria-label="会话操作">
      <button
        class="workspace-pill"
        type="button"
        :title="workspaceTitle"
        aria-label="打开工作区"
        @click="emit('openWorkspace')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3.5 6.5h6l2 2h9v9.5a2 2 0 0 1-2 2h-15v-13.5Z" />
          <path d="M3.5 8.5V6a2 2 0 0 1 2-2h3.2l1.8 2.5" />
        </svg>
        <span class="workspace-text">
          <span>{{ workspaceLabel }}</span>
          <small v-if="props.workspace">{{ currentDirLabel }} · {{ workspaceTrustLabel }}</small>
        </span>
      </button>
      <button
        v-if="props.workspace"
        class="icon-button"
        :class="{ active: isWorkspaceTrusted }"
        type="button"
        :aria-label="trustActionLabel"
        :title="trustActionLabel"
        @click="isWorkspaceTrusted ? emit('untrustWorkspace') : emit('trustWorkspace')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 3.5 19 6v5.4c0 4.3-2.8 7.8-7 9.1-4.2-1.3-7-4.8-7-9.1V6l7-2.5Z" />
          <path v-if="isWorkspaceTrusted" d="m9 12 2 2 4-4" />
          <path v-else d="M9 12h6" />
        </svg>
      </button>
      <button
        v-if="props.workspace"
        class="icon-button"
        type="button"
        aria-label="切换当前目录"
        title="切换当前目录"
        @click="emit('changeDirectory')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3.5 6.5h6l2 2h9v8.5a2 2 0 0 1-2 2h-15V6.5Z" />
          <path d="m9 13 3 3 3-3M12 16V9" />
        </svg>
      </button>
      <button
        v-if="props.workspace"
        class="icon-button"
        type="button"
        aria-label="加入额外目录"
        title="加入额外目录"
        @click="emit('addDirectory')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3.5 6.5h6l2 2h9v9.5a2 2 0 0 1-2 2h-15V6.5Z" />
          <path d="M12 12v6M9 15h6" />
        </svg>
      </button>
      <button
        v-if="isSuspended"
        class="icon-button"
        type="button"
        aria-label="恢复会话"
        @click="emit('resume')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 12a7 7 0 1 0 2.1-5M5 5v5h5" />
        </svg>
      </button>
      <button
        class="icon-button"
        type="button"
        :aria-label="canDisconnect ? '断开后端' : '连接后端'"
        @click="canDisconnect ? emit('disconnect') : emit('connect')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path
            v-if="canDisconnect"
            d="M6 8.5h8.5a4.5 4.5 0 0 1 0 9H13M18 15.5H9.5a4.5 4.5 0 0 1 0-9H11"
          />
          <path v-else d="M9 7H7.5a4.5 4.5 0 0 0 0 9H10M14 7h2.5a4.5 4.5 0 0 1 0 9H15M8 12h8" />
        </svg>
      </button>
      <button
        class="icon-button"
        type="button"
        aria-label="新建对话"
        @click="emit('newConversation')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
    </nav>
  </header>
</template>
