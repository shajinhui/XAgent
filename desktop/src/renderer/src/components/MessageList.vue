<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { renderMarkdown } from '@renderer/services/markdown'
import { useChatStore } from '@renderer/stores/chat'
import type { ActivityStepKind, ChatMessage } from '@renderer/stores/chat'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const transcript = ref<HTMLElement | null>(null)
const chat = useChatStore()

const activityIcons: Record<ActivityStepKind, string> = {
  thinking: 'M12 3a9 9 0 1 0 9 9h-2a7 7 0 1 1-7-7V3Zm1 5h-2v5l4 2 .9-1.8-2.9-1.4V8Z',
  search:
    'M10.5 4a6.5 6.5 0 0 0 0 13c1.5 0 2.9-.5 4-1.3l3.4 3.4 1.2-1.2-3.4-3.4a6.5 6.5 0 0 0-5.2-10.5Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z',
  read: 'M5 4h9l5 5v11H5V4Zm8 2H7v12h10v-8h-4V6Zm2 1.4V8h.6L15 7.4ZM8.5 12h7v1.6h-7V12Zm0 3h5v1.6h-5V15Z',
  edit: 'M17.7 3.3a2.4 2.4 0 0 1 3.4 3.4L9.4 18.4 4 20l1.6-5.4L17.7 3.3ZM7.3 15.6l-.6 2 2-.6L18.6 7 17.3 5.7 7.3 15.6Z',
  command:
    'M4 5h16v14H4V5Zm2 2v10h12V7H6Zm2 2.3L11 12l-3 2.7-1-1.1 1.8-1.6L7 10.4l1-1.1Zm4 4.2h4V15h-4v-1.5Z',
  permission:
    'M12 2 4.5 5.2V11c0 4.7 3.2 8.6 7.5 9.8 4.3-1.2 7.5-5.1 7.5-9.8V5.2L12 2Zm0 2.2 5.5 2.3V11c0 3.4-2.1 6.3-5.5 7.6-3.4-1.3-5.5-4.2-5.5-7.6V6.5L12 4.2Zm-1 4.3h2V13h-2V8.5Zm0 6h2v2h-2v-2Z',
  question:
    'M12 3a8 8 0 0 0-8 8v5.5A2.5 2.5 0 0 0 6.5 19H8v2.5L11.5 19H12a8 8 0 0 0 0-16Zm0 2a6 6 0 0 1 0 12h-1.2L10 17.6V17H6.5a.5.5 0 0 1-.5-.5V11a6 6 0 0 1 6-6Zm-1 9h2v2h-2v-2Zm1-7.5a2.7 2.7 0 0 1 1.2 5.1c-.5.3-.7.6-.7 1.2V13h-1.8v-.3c0-1.2.6-2 1.4-2.5.5-.3.9-.6.9-1.1 0-.6-.4-1-1-1-.7 0-1.1.5-1.1 1.2H9.1A2.8 2.8 0 0 1 12 6.5Z',
  web: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm6.7 8h-3.1a13 13 0 0 0-1-4.5A7 7 0 0 1 18.7 11ZM12 5.1c.7 1 1.3 3.1 1.5 5.9h-3C10.7 8.2 11.3 6.1 12 5.1ZM5.3 13h3.1c.1 1.6.4 3.1 1 4.5A7 7 0 0 1 5.3 13Zm3.1-2H5.3a7 7 0 0 1 4.1-4.5 13 13 0 0 0-1 4.5Zm1.9 2h3.4c-.3 3.1-1 5.1-1.7 5.8-.7-.7-1.4-2.7-1.7-5.8Zm4.3 4.5c.6-1.4.9-2.9 1-4.5h3.1a7 7 0 0 1-4.1 4.5Z',
  tool: 'M5 4h14v4H5V4Zm2 2v.5h10V6H7Zm-2 5h14v9H5v-9Zm2 2v5h10v-5H7Z',
  error: 'M12 3 2.5 20h19L12 3Zm0 4 5.8 11H6.2L12 7Zm-1 3h2v4h-2v-4Zm0 5h2v2h-2v-2Z'
}

function getActivityIcon(kind: ActivityStepKind): string {
  return activityIcons[kind] || activityIcons.tool
}

function isMessageHidden(message: ChatMessage): boolean {
  if (!message.activityGroupId || message.isFinal) return false

  const group = props.messages.find((item) => item.id === message.activityGroupId)
  return Boolean(group?.collapsed)
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  transcript.value?.scrollTo({
    top: transcript.value.scrollHeight,
    behavior: 'smooth'
  })
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  textarea.remove()
}

async function handleTranscriptClick(event: MouseEvent): Promise<void> {
  const target = event.target
  if (!(target instanceof Element)) return

  const button = target.closest<HTMLButtonElement>('[data-copy-code="true"]')
  if (!button) return

  const codeBlock = button.closest('.code-block')
  const code = codeBlock?.querySelector('pre code')
  const text = code?.textContent || ''
  if (!text) return

  try {
    await copyText(text)
    button.textContent = '已复制'
    button.classList.add('copied')
    window.setTimeout(() => {
      button.textContent = '复制'
      button.classList.remove('copied')
    }, 1400)
  } catch {
    button.textContent = '复制失败'
    window.setTimeout(() => {
      button.textContent = '复制'
    }, 1400)
  }
}

watch(
  () => [props.messages.length, props.messages[props.messages.length - 1]?.content],
  scrollToBottom,
  { flush: 'post' }
)
</script>

<template>
  <div ref="transcript" class="transcript" @click="handleTranscriptClick">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message"
      :class="[`message-${message.role}`, { 'message-hidden': isMessageHidden(message) }]"
    >
      <template v-if="message.role === 'activity'">
        <div class="activity-shell">
          <div class="activity-heading">
            <button
              class="activity-pill"
              type="button"
              :class="{
                'activity-pill-error': message.content.includes('出错')
              }"
              @click="chat.toggleActivity(message.id)"
            >
              <span>{{ message.content }}</span>
              <svg viewBox="0 0 24 24" aria-hidden="true" :class="{ expanded: !message.collapsed }">
                <path d="m9 18 6-6-6-6" />
              </svg>
            </button>
          </div>
        </div>
      </template>

      <template v-else-if="message.role === 'activity_event' && message.step">
        <div
          class="activity-line"
          :class="[`activity-${message.step.status}`, `activity-kind-${message.step.kind}`]"
        >
          <span class="activity-line-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path :d="getActivityIcon(message.step.kind)" />
            </svg>
          </span>
          <div class="activity-line-body">
            <span>{{ message.step.label }}</span>
            <details v-if="message.step.detail" class="activity-detail">
              <summary>详情</summary>
              <pre>{{ message.step.detail }}</pre>
            </details>
          </div>
        </div>
      </template>

      <template v-else>
        <div v-if="message.meta && message.role !== 'assistant'" class="message-meta">
          {{ message.meta }}
        </div>
        <!-- eslint-disable vue/no-v-html -->
        <div
          v-if="message.role === 'assistant'"
          class="bubble markdown-body"
          v-html="renderMarkdown(message.content)"
        ></div>
        <!-- eslint-enable vue/no-v-html -->
        <div v-else class="bubble">{{ message.content }}</div>
      </template>
    </article>
  </div>
</template>
