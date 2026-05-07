<script setup lang="ts">
import { computed, onMounted } from 'vue'
import ChatComposer from '@renderer/components/ChatComposer.vue'
import MessageList from '@renderer/components/MessageList.vue'
import PermissionDialog from '@renderer/components/PermissionDialog.vue'
import TitleBar from '@renderer/components/TitleBar.vue'
import { useChatStore } from '@renderer/stores/chat'
import { useRuntimeStore } from '@renderer/stores/runtime'

const chat = useChatStore()
const runtime = useRuntimeStore()

const composerPlaceholder = computed(() => {
  if (runtime.isSuspended) return '会话已挂起，请先恢复...'
  if (runtime.isConnecting) return '正在连接后端...'
  return '输入消息...'
})

onMounted(() => {
  void runtime.connect()
})
</script>

<template>
  <main class="app-screen">
    <section class="chat-window" aria-label="Codex-mini chat preview">
      <TitleBar
        :message-count="chat.messageCount"
        :connection-status="runtime.connectionStatus"
        :is-suspended="runtime.isSuspended"
        @connect="runtime.connect"
        @disconnect="runtime.disconnect"
        @resume="runtime.resumeSession"
      />
      <MessageList :messages="chat.messages" />
      <ChatComposer
        :disabled="runtime.isConnecting || runtime.isSuspended"
        :placeholder="composerPlaceholder"
        @send="runtime.sendUserInput"
      />
    </section>

    <PermissionDialog
      v-if="runtime.activePermission"
      :request="runtime.activePermission"
      @approve="runtime.approvePermission"
      @deny="runtime.denyPermission"
    />
  </main>
</template>
