<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage } from '@renderer/stores/chat'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const transcript = ref<HTMLElement | null>(null)

async function scrollToBottom(): Promise<void> {
  await nextTick()
  transcript.value?.scrollTo({
    top: transcript.value.scrollHeight,
    behavior: 'smooth'
  })
}

watch(
  () => [props.messages.length, props.messages[props.messages.length - 1]?.content],
  scrollToBottom,
  { flush: 'post' }
)
</script>

<template>
  <div ref="transcript" class="transcript">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message"
      :class="`message-${message.role}`"
    >
      <div v-if="message.meta" class="message-meta">{{ message.meta }}</div>
      <div class="bubble">{{ message.content }}</div>
    </article>
  </div>
</template>
