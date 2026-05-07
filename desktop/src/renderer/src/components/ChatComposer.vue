<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

const draft = ref('')

function sendMessage(): void {
  if (props.disabled) return

  const text = draft.value.trim()
  if (!text) return

  emit('send', text)
  draft.value = ''
}
</script>

<template>
  <form class="composer" @submit.prevent="sendMessage">
    <textarea
      v-model="draft"
      :placeholder="placeholder || '输入消息...'"
      :disabled="disabled"
      rows="3"
      @keydown.enter.exact.prevent="sendMessage"
    ></textarea>

    <div class="composer-actions">
      <div class="left-tools">
        <button class="icon-button soft" type="button" aria-label="添加附件">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m7.5 13.5 6.6-6.6a3 3 0 0 1 4.2 4.2l-8.1 8.1a4.5 4.5 0 0 1-6.4-6.4l8.2-8.2" />
          </svg>
        </button>
        <button class="icon-button soft active" type="button" aria-label="Agent 模式">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 3v4M12 17v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M3 12h4M17 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"
            />
          </svg>
        </button>
      </div>

      <div class="model-pill">
        <span>AI</span>
        <strong>{{ disabled ? 'waiting' : 'runtime' }}</strong>
      </div>

      <button class="send-button" type="submit" aria-label="发送消息" :disabled="disabled">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 11.5 20 4l-7.5 16-1.8-6.7z" />
        </svg>
      </button>
    </div>
  </form>
</template>
