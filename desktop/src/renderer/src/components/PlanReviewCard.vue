<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Check, Circle, CircleAlert, LoaderCircle, Play, X } from '@lucide/vue'
import IconButton from '@renderer/components/ui/IconButton.vue'
import type { PlanPendingEvent, RuntimeTaskListItem } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  plan: PlanPendingEvent
  disabled?: boolean
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

type PlanItemStatus = RuntimeTaskListItem['status']

const planItems = computed(() => props.plan.items || [])
const hasPlanDocument = computed(() => Boolean(props.plan.plan_markdown?.trim()))
const planSummary = computed(() => props.plan.summary?.trim() || props.plan.content)

function getStatusIcon(status: PlanItemStatus): Component {
  if (status === 'completed') return Check
  if (status === 'in_progress') return LoaderCircle
  if (status === 'error') return CircleAlert
  return Circle
}
</script>

<template>
  <section class="plan-review-card" role="group" aria-labelledby="plan-review-title">
    <header class="plan-review-heading">
      <div>
        <h2 id="plan-review-title">计划待确认</h2>
        <p>{{ planSummary }}</p>
      </div>
      <IconButton
        class="plan-icon-action"
        label="取消计划"
        :disabled="disabled"
        @click="emit('cancel')"
      >
        <X />
      </IconButton>
    </header>

    <div v-if="hasPlanDocument" class="plan-review-note">
      上方计划书已生成。确认后我会按这份计划开始实施。
    </div>

    <div v-else class="plan-review-steps">
      <div
        v-for="(item, index) in planItems"
        :key="`${index}:${item.step}`"
        class="plan-review-step"
        :class="`is-${item.status}`"
      >
        <span class="plan-review-step-icon">
          <component :is="getStatusIcon(item.status)" />
        </span>
        <span>{{ item.step }}</span>
      </div>
    </div>

    <footer class="plan-review-actions">
      <button
        class="plan-secondary-action"
        type="button"
        :disabled="disabled"
        @click="emit('cancel')"
      >
        取消
      </button>
      <button
        class="plan-primary-action"
        type="button"
        :disabled="disabled"
        @click="emit('confirm')"
      >
        <Play />
        <span>实施计划</span>
      </button>
    </footer>
  </section>
</template>
