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
        :title="props.workspace?.root || '打开工作区'"
        aria-label="打开工作区"
        @click="emit('openWorkspace')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3.5 6.5h6l2 2h9v9.5a2 2 0 0 1-2 2h-15v-13.5Z" />
          <path d="M3.5 8.5V6a2 2 0 0 1 2-2h3.2l1.8 2.5" />
        </svg>
        <span>{{ workspaceLabel }}</span>
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
