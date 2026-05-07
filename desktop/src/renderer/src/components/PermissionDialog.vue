<script setup lang="ts">
import { computed } from 'vue'
import type { PermissionRequestEvent } from '@renderer/types/runtimeEvents'

const props = defineProps<{
  request: PermissionRequestEvent
}>()

const emit = defineEmits<{
  approve: []
  deny: []
}>()

const formattedArguments = computed(() => {
  try {
    return JSON.stringify(JSON.parse(props.request.arguments), null, 2)
  } catch {
    return props.request.arguments
  }
})
</script>

<template>
  <div class="permission-backdrop" role="presentation">
    <section
      class="permission-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-title"
    >
      <header>
        <p>需要确认</p>
        <h2 id="permission-title">{{ request.tool }}</h2>
      </header>

      <p class="permission-detail">{{ request.detail }}</p>

      <pre v-if="formattedArguments" class="permission-args">{{ formattedArguments }}</pre>

      <footer>
        <button class="secondary-button" type="button" @click="emit('deny')">拒绝</button>
        <button class="primary-button" type="button" @click="emit('approve')">允许</button>
      </footer>
    </section>
  </div>
</template>
