<script setup lang="ts">
/**
 * A panel with a title bar, the way a tiled DAW is built.
 *
 * FL Studio's workspace is windows: every tool has a caption strip, a colour
 * and a collapse box, and you read the screen by scanning those strips. The
 * studio's old flat sections had none of that - at a glance you could not
 * tell where the arrangement ended and the rack began, which is exactly the
 * complaint about it feeling minimal.
 *
 * The accent colour is per panel and deliberately not decoration: it is how
 * you find the mixer in peripheral vision when six panels are open.
 *
 * The frame is drawn as a rack unit rather than a card: a brushed caption
 * strip with a screw at each end, lettering engraved into it, and a lit
 * indicator for whether the unit is open. None of that is skeuomorphism for
 * its own sake - it is the difference between a panel the eye files under
 * "web page section" and one it files under "device". The screws in
 * particular do most of the work for how little they cost, because a rack
 * ear is the single most recognisable shape in a studio.
 */
import { ref } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    /** CSS colour for the caption bar's edge and glyph. */
    accent?: string
    /** Collapsed panels keep their caption strip, like a rolled-up window. */
    collapsible?: boolean
    startCollapsed?: boolean
    /** Right-hand text in the caption - counts, state, a value. */
    badge?: string
    dense?: boolean
  }>(),
  { accent: 'var(--color-accent1)', collapsible: true, startCollapsed: false, dense: false },
)

const collapsed = ref(props.startCollapsed)
const root = ref<HTMLElement | null>(null)

/**
 * Opened from outside.
 *
 * A panel that starts collapsed is invisible to anyone who does not already
 * know it is there, so the things that point at one - the transport's invite
 * button, a multiplayer project opening for the first time - need to be able
 * to roll it down and put it in front of the user.
 */
function reveal() {
  collapsed.value = false
  root.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

defineExpose({ reveal })
</script>

<template>
  <section
    ref="root"
    class="card flex min-h-0 flex-col overflow-hidden"
    :style="{ boxShadow: collapsed
      ? 'inset 0 1px 0 0 var(--lip), inset 0 -1px 0 0 var(--shade)'
      : 'inset 0 1px 0 0 var(--lip), inset 0 -1px 0 0 var(--shade), 0 4px 14px -8px rgba(0,0,0,0.9)' }"
  >
    <!-- Caption strip. Short, dense, and the same height on every panel so
         the eye can run down a column of them. -->
    <!-- The whole caption toggles, not just the glyph. FL rolls a window up
         when you click its title bar, and a 9px arrow is a poor target in a
         screen full of panels. Clicks on the actions slot are excluded: the
         buttons in there are not a request to collapse anything. -->
    <header
      class="rack-strip relative flex h-[26px] shrink-0 select-none items-center gap-2 pl-5 pr-5"
      :class="collapsible ? 'cursor-pointer' : ''"
      :style="{ borderLeft: `2px solid ${accent}` }"
      @click="collapsible && (collapsed = !collapsed)"
    >
      <!-- Rack ears. A recessed cross-head: dark well, light rim, and a
           notch across it. Four gradients, no image. -->
      <span class="screw screw-l"></span>
      <span class="screw screw-r"></span>

      <!-- Power lamp. Lit when the unit is open, dark when it is rolled up,
           which is how you read a rack from across the room. -->
      <span
        v-if="collapsible"
        class="led h-[5px] w-[5px] shrink-0 transition-colors"
        :style="{
          background: collapsed ? '#10161d' : accent,
          color: collapsed ? 'transparent' : accent,
        }"
      ></span>

      <h3
        class="engraved truncate text-[10px] font-semibold uppercase"
        :style="{ color: accent }"
      >{{ title }}</h3>

      <span v-if="badge" class="readout-dim truncate text-[10px]">{{ badge }}</span>

      <div class="ml-auto flex items-center gap-1.5" @click.stop>
        <slot name="actions" />
      </div>
    </header>

    <div v-show="!collapsed" class="min-h-0 flex-1 overflow-auto" :class="dense ? '' : 'p-2'">
      <slot />
    </div>
  </section>
</template>

<style scoped>
/*
 * Scoped rather than global: a screw is a thing this frame draws, and
 * nothing else in the app should be reaching for it.
 *
 * Read from the outside in - a bright rim catching the light from above, a
 * dark countersunk well, then the slot cut across it. The rim is the top
 * half only, because a real screw head is lit the same way the panel is.
 */
.screw-l { left: 7px; }
.screw-r { right: 7px; }

.screw {
  position: absolute;
  top: 50%;
  width: 6px;
  height: 6px;
  margin-top: -3px;
  border-radius: 50%;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.16), rgba(0, 0, 0, 0.5)),
    #0c1118;
  box-shadow:
    inset 0 -0.5px 0.5px rgba(255, 255, 255, 0.12),
    0 0.5px 0 rgba(255, 255, 255, 0.05);
}

/* The slot. One pseudo-element, rotated, so every panel's screws are not
   all driven to the same angle - which is the detail that makes a row of
   them look printed rather than assembled. */
.screw::after {
  content: '';
  position: absolute;
  inset: 1.5px 0.5px;
  border-top: 1px solid rgba(0, 0, 0, 0.75);
  transform: rotate(28deg);
}

.screw-r::after {
  transform: rotate(-52deg);
}
</style>
