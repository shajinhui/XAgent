<script setup lang="ts">
import { nextTick, ref, watch, type Component } from 'vue'
import {
  BrainCircuit,
  ChevronRight,
  FileText,
  Globe,
  MessageCircleQuestion,
  PencilLine,
  Search,
  ShieldAlert,
  Terminal,
  TriangleAlert,
  Wrench
} from 'lucide-vue-next'
import { renderMarkdown } from '@renderer/services/markdown'
import { useChatStore } from '@renderer/stores/chat'
import type { ActivityStepKind, ChatMessage } from '@renderer/stores/chat'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const transcript = ref<HTMLElement | null>(null)
const chat = useChatStore()

const activityIcons: Record<ActivityStepKind, Component> = {
  thinking: BrainCircuit,
  search: Search,
  read: FileText,
  edit: PencilLine,
  command: Terminal,
  permission: ShieldAlert,
  question: MessageCircleQuestion,
  web: Globe,
  tool: Wrench,
  error: TriangleAlert
}

function getActivityIcon(kind: ActivityStepKind): Component {
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
              <ChevronRight :class="{ expanded: !message.collapsed }" />
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
            <component :is="getActivityIcon(message.step.kind)" />
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
