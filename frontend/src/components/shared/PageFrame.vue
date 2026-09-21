<script setup lang="ts">
/**
 * The chrome every non-DAW page wears.
 *
 * Each of these pages used to open with its own centred column, its own
 * top margin and its own `<h1>` in a different size - so moving between
 * them felt like moving between documents on a website rather than between
 * views of one program. The pages themselves were fine; the framing was
 * the problem.
 *
 * Three rules, all borrowed from the workstation:
 *
 *   - The title strip is pinned. It names where you are and holds the
 *     controls that act on the whole view, and it does not scroll away.
 *   - The body fills the window and scrolls inside itself. A page that
 *     scrolls as a document takes its header with it, which is why the
 *     header stops being a place you can rely on finding things.
 *   - No centred max-width column. These are tool views on a monitor that
 *     is already the right width; a 1280px cap in the middle of a 2560px
 *     screen is a magazine layout.
 */
withDefaults(
  defineProps<{
    title: string
    /** One line under the title. Says what the view is for, once. */
    subtitle?: string
    /** Accent for the strip's edge and title, so the pages are
     *  distinguishable in peripheral vision the way the DAW's panels are. */
    accent?: string
    /** Pages that manage their own padding (a full-bleed list, a grid that
     *  runs to the edges) turn the body gutter off. */
    flush?: boolean
  }>(),
  { accent: 'var(--color-accent1)', flush: false },
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <header
      class="rack-strip flex shrink-0 items-center gap-3 px-3 py-2"
      :style="{ borderLeft: `2px solid ${accent}` }"
    >
      <div class="min-w-0">
        <h1
          class="engraved truncate text-[12px] font-semibold uppercase"
          :style="{ color: accent }"
        >{{ title }}</h1>
        <p v-if="subtitle" class="truncate text-[11px] text-text-dim">{{ subtitle }}</p>
      </div>

      <!-- Whole-view controls: an engine switch, a connect button, a
           format picker. Right-aligned because that is where a title bar
           puts the things that act on what it names. -->
      <div class="ml-auto flex shrink-0 items-center gap-2">
        <slot name="actions" />
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto" :class="flush ? '' : 'p-3'">
      <slot />
    </div>
  </div>
</template>
