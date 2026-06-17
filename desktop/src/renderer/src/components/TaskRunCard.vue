<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Check, ChevronDown, Circle, CircleAlert, LoaderCircle } from '@lucide/vue'
import type { ActivityStepStatus, ChatMessage } from '@renderer/stores/chat'
import type { RuntimeTaskListItem } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  messages: ChatMessage[]
  activeActivityMessageId: number | null
}>()

type TaskItemStatus = 'completed' | 'in_progress' | 'pending' | 'error'

type TaskItem = {
  key: string
  label: string
  status: TaskItemStatus
}

const activeActivity = computed(() =>
  props.messages.find((message) => message.id === props.activeActivityMessageId)
)

const activityEvents = computed(() =>
  props.messages.filter(
    (message) =>
      message.role === 'activity_event' &&
      message.activityGroupId === props.activeActivityMessageId &&
      message.step
  )
)

const latestStatus = computed<ActivityStepStatus>(() => {
  const last = activityEvents.value[activityEvents.value.length - 1]
  return last?.step?.status || (activeActivity.value ? 'running' : 'success')
})

const cardStatus = computed<ActivityStepStatus>(() => {
  if (latestStatus.value === 'waiting' || latestStatus.value === 'error') return latestStatus.value
  return 'running'
})

const statusLabel = computed(() => {
  if (cardStatus.value === 'waiting') return '等待确认'
  if (cardStatus.value === 'error') return '遇到问题'
  return '运行中'
})

const taskList = computed<TaskItem[]>(() => {
  const items = activeActivity.value?.taskList || []
  if (!items.length) {
    return [
      {
        key: 'tasklist-loading',
        label: '正在拆解任务',
        status: 'in_progress'
      }
    ]
  }

  return items.map((item, index) => ({
    key: `task:${index}:${item.step}`,
    label: item.step,
    status: normalizeTaskStatus(item)
  }))
})

function normalizeTaskStatus(item: RuntimeTaskListItem): TaskItemStatus {
  if (item.status === 'completed' || item.status === 'error') return item.status
  if (item.status === 'in_progress') return 'in_progress'
  return 'pending'
}

function getStepIcon(status: TaskItemStatus): Component {
  if (status === 'completed') return Check
  if (status === 'in_progress') return LoaderCircle
  if (status === 'error') return CircleAlert
  return Circle
}
</script>

<template>
  <aside
    v-if="activeActivity"
    class="task-run-card"
    :class="[`task-run-card-${cardStatus}`]"
    aria-live="polite"
  >
    <header class="task-run-card-header">
      <button class="task-run-tab" type="button" aria-label="当前进度">
        <span>进度</span>
        <ChevronDown aria-hidden="true" />
      </button>
      <span class="task-run-card-subtitle">{{ statusLabel }} · {{ activeActivity.content }}</span>
    </header>

    <div class="task-run-steps">
      <div
        v-for="item in taskList"
        :key="item.key"
        class="task-run-step"
        :class="`is-${item.status}`"
      >
        <span class="task-run-step-icon">
          <component :is="getStepIcon(item.status)" />
        </span>
        <span>{{ item.label }}</span>
      </div>
    </div>
  </aside>
</template>
