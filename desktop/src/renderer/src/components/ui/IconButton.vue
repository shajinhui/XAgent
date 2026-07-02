<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    label: string
    title?: string
    type?: 'button' | 'submit' | 'reset'
    active?: boolean
    disabled?: boolean
    pressed?: boolean | 'mixed'
    variant?: 'ghost' | 'soft' | 'danger'
    size?: 'sm' | 'md' | 'lg'
  }>(),
  {
    type: 'button',
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
    :type="props.type"
    :aria-label="props.label"
    :aria-pressed="props.pressed"
    :title="props.title || props.label"
    :disabled="props.disabled"
  >
    <slot />
  </button>
</template>
