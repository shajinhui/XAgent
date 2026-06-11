<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    label: string
    title?: string
    active?: boolean
    disabled?: boolean
    variant?: 'ghost' | 'soft' | 'danger'
    size?: 'sm' | 'md' | 'lg'
  }>(),
  {
    active: false,
    disabled: false,
    variant: 'ghost',
    size: 'md'
  }
)
</script>

<template>
  <button
    class="icon-button"
    :class="[
      `icon-button-${props.size}`,
      {
        active: props.active,
        soft: props.variant === 'soft',
        danger: props.variant === 'danger'
      }
    ]"
    type="button"
    :aria-label="props.label"
    :title="props.title || props.label"
    :disabled="props.disabled"
  >
    <slot />
  </button>
</template>
