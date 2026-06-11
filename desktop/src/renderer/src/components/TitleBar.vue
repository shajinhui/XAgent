<script setup lang="ts">
import { computed } from 'vue'
import {
  FolderInput,
  FolderOpen,
  FolderPlus,
  Link,
  PanelLeft,
  Plus,
  RefreshCw,
  ShieldCheck,
  ShieldMinus,
  Unlink
} from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
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
  if (level === 'trusted') return '已信任'
  if (level === 'untrusted') return '未信任'
  return '本会话'
})

const isWorkspaceTrusted = computed(() => props.workspace?.trust.level === 'trusted')

const trustActionLabel = computed(() =>
  isWorkspaceTrusted.value ? '取消信任当前项目' : '信任当前项目'
)

const workspaceTitle = computed(() => {
  if (!props.workspace) return '打开工作区'
  return `${props.workspace.display_name} · ${workspaceTrustLabel.value}`
})
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
      <PanelLeft />
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
        <FolderOpen />
        <span class="workspace-text">
          <span>{{ workspaceLabel }}</span>
          <small v-if="props.workspace">{{ workspaceTrustLabel }}</small>
        </span>
      </button>
      <IconButton
        v-if="props.workspace"
        :label="trustActionLabel"
        :active="isWorkspaceTrusted"
        :title="trustActionLabel"
        @click="isWorkspaceTrusted ? emit('untrustWorkspace') : emit('trustWorkspace')"
      >
        <ShieldCheck v-if="isWorkspaceTrusted" />
        <ShieldMinus v-else />
      </IconButton>
      <IconButton
        v-if="props.workspace"
        label="切换当前目录"
        title="切换当前目录"
        @click="emit('changeDirectory')"
      >
        <FolderInput />
      </IconButton>
      <IconButton
        v-if="props.workspace"
        label="加入额外目录"
        title="加入额外目录"
        @click="emit('addDirectory')"
      >
        <FolderPlus />
      </IconButton>
      <IconButton v-if="isSuspended" label="恢复会话" @click="emit('resume')">
        <RefreshCw />
      </IconButton>
      <IconButton
        :label="canDisconnect ? '断开后端' : '连接后端'"
        @click="canDisconnect ? emit('disconnect') : emit('connect')"
      >
        <Unlink v-if="canDisconnect" />
        <Link v-else />
      </IconButton>
      <IconButton label="新建对话" @click="emit('newConversation')">
        <Plus />
      </IconButton>
    </nav>
  </header>
</template>
