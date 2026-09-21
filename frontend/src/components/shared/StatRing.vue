<script setup lang="ts">
/**
 * A percentage as a ring.
 *
 * Null is a first-class value here, not zero. A counter that failed to read
 * and a genuinely idle GPU look identical if both render as an empty ring,
 * and this widget exists to answer "can I start a generation" - so it says
 * "-" when it does not know.
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number | null
    label: string
    caption?: string
    size?: number
    /** Turns the ring amber then rose as it fills. For load, where high is a
     *  warning; leave off for things where high is simply a fact. */
    warn?: boolean
  }>(),
  { size: 92, warn: true },
)

const RADIUS = 42
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const clamped = computed(() =>
  props.value == null ? null : Math.max(0, Math.min(100, props.value)),
)

const dash = computed(() =>
  clamped.value == null ? 0 : (clamped.value / 100) * CIRCUMFERENCE,
)

const stroke = computed(() => {
  if (clamped.value == null) return 'var(--color-text-faint)'
  if (!props.warn) return 'var(--color-accent1)'
  if (clamped.value >= 90) return 'var(--color-status-failed)'
  if (clamped.value >= 70) return 'var(--color-status-queued)'
  return 'var(--color-accent1)'
})
</script>

<template>
  <div class="flex flex-col items-center gap-1">
    <div class="relative" :style="{ width: `${size}px`, height: `${size}px` }">
      <svg viewBox="0 0 100 100" class="h-full w-full -rotate-90">
        <circle cx="50" cy="50" :r="RADIUS" fill="none" stroke="var(--color-panel-3)" stroke-width="8" />
        <circle
          cx="50"
          cy="50"
          :r="RADIUS"
          fill="none"
          :stroke="stroke"
          stroke-width="8"
          stroke-linecap="round"
          :stroke-dasharray="`${dash} ${CIRCUMFERENCE}`"
          class="transition-[stroke-dasharray,stroke] duration-500 ease-out"
        />
      </svg>
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <span class="text-lg font-semibold tabular-nums text-text">
          {{ clamped == null ? '-' : `${Math.round(clamped)}%` }}
        </span>
      </div>
    </div>
    <span class="text-[11px] font-medium text-text-dim">{{ label }}</span>
    <span v-if="caption" class="text-[10px] tabular-nums text-text-faint">{{ caption }}</span>
  </div>
</template>
