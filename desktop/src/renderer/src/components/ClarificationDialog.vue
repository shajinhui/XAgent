<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  ClarificationOption,
  ClarificationRequestEvent,
  ClarificationResponsePayload
} from '@renderer/types/runtimeEvents'

const props = defineProps<{
  request: ClarificationRequestEvent
}>()

const emit = defineEmits<{
  answer: [payload: ClarificationResponsePayload]
  skip: []
}>()

const selectedIndex = ref(defaultOptionIndex(props.request.options))
const freeformAnswer = ref('')

const options = computed(() => props.request.options || [])
const hasOptions = computed(() => options.value.length > 0)
const canSubmit = computed(
  () =>
    selectedIndex.value >= 0 ||
    (props.request.allow_freeform && Boolean(freeformAnswer.value.trim()))
)

watch(
  () => props.request.request_id,
  () => {
    selectedIndex.value = defaultOptionIndex(props.request.options)
    freeformAnswer.value = ''
  }
)

function defaultOptionIndex(options: ClarificationOption[]): number {
  const recommendedIndex = options.findIndex((option) => option.recommended)
  if (recommendedIndex >= 0) return recommendedIndex
  return options.length ? 0 : -1
}

function selectOption(index: number): void {
  selectedIndex.value = index
}

function submitAnswer(): void {
  if (!canSubmit.value) return

  const option = options.value[selectedIndex.value]
  const answer = freeformAnswer.value.trim()
  emit('answer', {
    choice_id: option?.id,
    option_index: option ? selectedIndex.value : undefined,
    content: answer || option?.label,
    skipped: false
  })
}
</script>

<template>
  <section class="clarification-composer" role="group" aria-labelledby="clarification-title">
    <header class="clarification-heading">
      <p id="clarification-title">{{ request.question }}</p>
    </header>

    <div v-if="hasOptions" class="clarification-options" aria-label="回答选项">
      <button
        v-for="(option, index) in options"
        :key="option.id || `${index}-${option.label}`"
        type="button"
        class="clarification-option"
        :class="{ selected: selectedIndex === index }"
        @click="selectOption(index)"
      >
        <span>{{ index + 1 }}.</span>
        <strong>
          {{ option.label }}
          <small v-if="option.recommended">推荐</small>
        </strong>
        <em v-if="option.description">{{ option.description }}</em>
      </button>
    </div>

    <textarea
      v-if="request.allow_freeform"
      v-model="freeformAnswer"
      rows="2"
      placeholder="补充说明..."
      @keydown.enter.meta.prevent="submitAnswer"
      @keydown.enter.ctrl.prevent="submitAnswer"
    ></textarea>

    <footer class="clarification-actions">
      <button class="clarification-skip" type="button" @click="emit('skip')">跳过</button>
      <button
        class="clarification-submit"
        type="button"
        :disabled="!canSubmit"
        @click="submitAnswer"
      >
        继续
      </button>
    </footer>
  </section>
</template>
