<script setup lang="ts">
/**
 * The persistent left rail.
 *
 * Replaces the old top bar, which had run out of room: seven destinations,
 * two engine chips and a language picker were wrapping onto a second line on
 * a 1280px window and stealing vertical space from the timeline - the one
 * part of a DAW that can never have enough of it. A vertical rail costs
 * width, which this layout has, instead of height, which it does not.
 *
 * Collapsible to icons, remembered per machine. Anyone working on a laptop
 * with the mixer open will collapse it within a day, and having to do that on
 * every launch would be its own small insult.
 */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { MODEL_LABELS, useModelSwitch } from '../../composables/useModelSwitch'
import { setLocale, currentLocale, type LocaleCode } from '../../i18n'
import type { ModelId, ModelRuntimeStatus } from '../../types'

const orchestrator = useOrchestratorStore()
const route = useRoute()
const { selectModel } = useModelSwitch()
const { t } = useI18n()

const COLLAPSE_KEY = 'cashout_sidebar_collapsed'
const collapsed = ref(readCollapsed())

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
  } catch {
    // Private browsing - it just won't be remembered.
  }
}

/** Inline SVG rather than an icon package: this app ships offline, and a
 *  webfont or a 400-icon dependency for eight glyphs is not a trade worth
 *  making. Each is a single path on a 24-unit grid. */
interface NavItem {
  to: string
  key: string
  path: string
  match: string
}

const NAV: NavItem[] = [
  { to: '/', key: 'nav.home', match: '^/$',
    path: 'M3 10.5 12 3l9 7.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z' },
  { to: '/editor', key: 'nav.daw', match: '^/editor',
    path: 'M4 6h2v12H4zm4 3h2v6H8zm4-5h2v16h-2zm4 4h2v8h-2zm4 3h2v2h-2z' },
  { to: '/stable-audio', key: 'nav.beats', match: '^/stable-audio',
    path: 'M12 3a9 9 0 1 0 9 9h-2a7 7 0 1 1-7-7zm0 4a5 5 0 1 0 5 5h-2a3 3 0 1 1-3-3z' },
  { to: '/treblo', key: 'nav.treblo', match: '^/treblo',
    path: 'M12 3v10.55A4 4 0 1 0 14 17V7h4V3z' },
  { to: '/ace-step', key: 'nav.generate', match: '^/ace-step$',
    path: 'm12 2 2.2 5.6L20 9.8l-5.8 2.2L12 18l-2.2-6L4 9.8l5.8-2.2z' },
  { to: '/ace-step/lora', key: 'nav.lora', match: '^/ace-step/lora',
    path: 'M4 19h16v2H4zm2-4h3v3H6zm5-5h3v8h-3zm5-6h3v14h-3z' },
  { to: '/voices', key: 'nav.voices', match: '^/voices',
    path: 'M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11z' },
  { to: '/separation', key: 'nav.separation', match: '^/separation',
    path: 'M3 5h18v2H3zm0 6h8v2H3zm10 0h8v2h-8zM3 17h18v2H3z' },
]

/* Help and About are navigation too, but not of the same kind: they are
   about the program rather than places you work. They sit below the rule
   with the engine lamps, where you look when something is wrong rather
   than when you are trying to get somewhere. */
const META_NAV: NavItem[] = [
  { to: '/help', key: 'nav.help', match: '^/help',
    path: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm.9 15h-1.8v-1.8h1.8zm1.9-6.9-.8.8c-.6.6-1 1.1-1 2.1h-1.8v-.5c0-.7.4-1.4 1-2l1.1-1.2a1.8 1.8 0 1 0-3.1-1.3H8.4a3.6 3.6 0 1 1 6.4 2.1z' },
  { to: '/about', key: 'nav.about', match: '^/about',
    path: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z' },
]

const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']

function isActive(item: NavItem): boolean {
  return new RegExp(item.match).test(route.path)
}

function statusOf(id: ModelId): ModelRuntimeStatus {
  return orchestrator.statuses[id]?.status ?? 'stopped'
}

const LED_CLASSES: Record<ModelRuntimeStatus, string> = {
  // Both a background and a text colour: `.led` draws its glow from
  // currentColor, so an LED given only a fill is a flat dot again.
  stopped: 'bg-[#0c1219] text-transparent',
  starting: 'bg-status-queued text-status-queued breathe',
  running: 'bg-status-done text-status-done',
  stopping: 'bg-status-queued text-status-queued breathe',
  error: 'bg-status-failed text-status-failed',
}

const STATUS_LABEL_KEYS: Record<ModelRuntimeStatus, string> = {
  stopped: 'modelStatus.stopped',
  starting: 'modelStatus.starting',
  running: 'modelStatus.running',
  stopping: 'modelStatus.stopping',
  error: 'modelStatus.error',
}

const anyRunning = computed(() => MODEL_IDS.some((id) => statusOf(id) === 'running'))

async function onSelect(id: ModelId) {
  try {
    await selectModel(id)
  } catch {
    // orchestrator.switchError holds the message; the engines block shows it.
  }
}

const LOCALES: { code: LocaleCode; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ru', label: 'Русский' },
]

function onLocaleChange(e: Event) {
  setLocale((e.target as HTMLSelectElement).value as LocaleCode)
}
</script>

<template>
  <!-- The side cheek of the desk. It was a floating glass panel, which is
       the one element on screen that most has to look like part of the
       chassis: everything else is mounted in it. Brushed vertically, with a
       hard machined edge where the work surface begins. -->
  <aside
    class="sidebar-rail sticky top-0 z-40 flex h-svh shrink-0 flex-col transition-[width] duration-200"
    :class="collapsed ? 'w-14' : 'w-52'"
  >
    <!-- Brand -->
    <router-link
      to="/"
      class="rack-strip flex shrink-0 items-center gap-2.5 px-2.5 py-2.5 text-text"
      :title="collapsed ? 'Cashout Studio' : undefined"
    >
      <img src="/cashout-studio-logo.svg" alt="" class="h-7 w-7 shrink-0 rounded-sm" />
      <span v-if="!collapsed" class="flex min-w-0 flex-col leading-tight">
        <!-- Badged rather than titled. A maker's plate on a console is set
             small, in caps, and spaced out; that is the one place a brand
             name belongs on a piece of equipment. -->
        <span class="engraved truncate text-[11px] font-semibold uppercase">Cashout Studio</span>
        <span class="truncate text-[9px] uppercase tracking-[0.08em] text-text-faint">
          {{ t('header.tagline') }}
        </span>
      </span>
    </router-link>

    <!-- Destinations -->
    <nav class="mt-1.5 flex min-h-0 flex-1 flex-col gap-px overflow-y-auto px-1.5">
      <router-link
        v-for="item in NAV"
        :key="item.to"
        :to="item.to"
        :title="collapsed ? t(item.key) : undefined"
        class="nav-slot group relative flex items-center gap-2.5 px-2.5 py-1.5 text-[13px]"
        :class="isActive(item) ? 'nav-slot-on text-text' : 'text-text-dim hover:text-text'"
      >
        <!-- The active marker is a bar on the rail edge, not a filled pill:
             at this density a solid block on every hover is visual noise.
             Lit rather than painted, so it speaks the same indicator
             language as the engine lamps below it. -->
        <span
          v-if="isActive(item)"
          class="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 bg-accent1"
          style="box-shadow: 0 0 6px var(--color-accent1)"
        ></span>
        <svg viewBox="0 0 24 24" class="h-[18px] w-[18px] shrink-0" aria-hidden="true">
          <path :d="item.path" :fill="isActive(item) ? 'var(--color-accent1)' : 'currentColor'" />
        </svg>
        <span v-if="!collapsed" class="truncate">{{ t(item.key) }}</span>
      </router-link>
    </nav>

    <div class="divider-engraved flex flex-col gap-px px-1.5 pt-1.5">
      <router-link
        v-for="item in META_NAV"
        :key="item.to"
        :to="item.to"
        :title="collapsed ? t(item.key) : undefined"
        class="nav-slot relative flex items-center gap-2.5 px-2.5 py-1 text-[12px]"
        :class="isActive(item) ? 'nav-slot-on text-text' : 'text-text-dim hover:text-text'"
      >
        <span
          v-if="isActive(item)"
          class="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 bg-accent1"
          style="box-shadow: 0 0 6px var(--color-accent1)"
        ></span>
        <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" aria-hidden="true">
          <path :d="item.path" :fill="isActive(item) ? 'var(--color-accent1)' : 'currentColor'" />
        </svg>
        <span v-if="!collapsed" class="truncate">{{ t(item.key) }}</span>
      </router-link>
    </div>

    <!-- Engines. Kept at the bottom because it is status, not navigation -
         you check it when something is wrong, not to get somewhere. -->
    <div class="divider-engraved mt-2 px-1.5 py-2">
      <p v-if="!collapsed" class="engraved px-1.5 pb-1.5 text-[9px] font-semibold uppercase text-text-faint">
        {{ t('nav.engines') }}
      </p>
      <button
        v-for="id in MODEL_IDS"
        :key="id"
        type="button"
        class="nav-slot flex w-full items-center gap-2.5 px-2.5 py-1 text-left text-[11px] text-text-dim hover:text-text"
        :title="`${MODEL_LABELS[id]} · ${t(STATUS_LABEL_KEYS[statusOf(id)])}`"
        @click="onSelect(id)"
      >
        <span class="led h-[6px] w-[6px] shrink-0" :class="LED_CLASSES[statusOf(id)]"></span>
        <span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ MODEL_LABELS[id] }}</span>
        <span v-if="!collapsed" class="shrink-0 text-[10px] text-text-faint">
          {{ t(STATUS_LABEL_KEYS[statusOf(id)]) }}
        </span>
      </button>
      <p
        v-if="!collapsed && !anyRunning"
        class="px-1.5 pt-1 text-[10px] leading-snug text-text-faint"
      >
        {{ t('nav.noEngine') }}
      </p>
      <!-- One server hosts several models, which is not obvious from a
           single row - and someone hunting for YuE2 should not have to
           guess that "Audio engine" is where it lives. -->
      <p v-if="!collapsed" class="px-1.5 pt-1 text-[10px] leading-snug text-text-faint">
        {{ t('nav.engineHosts') }}
      </p>
    </div>

    <!-- Footer -->
    <div class="divider-engraved flex items-center gap-1 px-2 py-2">
      <select
        v-if="!collapsed"
        :value="currentLocale()"
        class="well min-w-0 flex-1 px-1.5 py-1 text-[11px] text-text-dim"
        @change="onLocaleChange"
      >
        <option v-for="locale in LOCALES" :key="locale.code" :value="locale.code">{{ locale.label }}</option>
      </select>
      <button
        type="button"
        class="key p-1.5"
        :title="collapsed ? t('nav.expand') : t('nav.collapse')"
        @click="toggleCollapsed"
      >
        <svg viewBox="0 0 24 24" class="h-4 w-4" aria-hidden="true">
          <path
            :d="collapsed ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>
  </aside>
</template>

<style scoped>
/*
 * The rail is brushed along its length, which is the axis the metal would
 * have been drawn in, and lit from the left so it turns away from the work
 * surface. The right edge is two lines - black, then a hairline of white -
 * which is the join between the cheek and the panel it carries.
 */
.sidebar-rail {
  background:
    linear-gradient(90deg,
      color-mix(in oklab, #fff 3.5%, var(--color-panel)) 0%,
      var(--color-panel) 45%,
      color-mix(in oklab, #000 30%, var(--color-panel)) 100%);
  border-right: 1px solid #000;
  box-shadow:
    inset -1px 0 0 rgba(255, 255, 255, 0.04),
    2px 0 8px -4px rgba(0, 0, 0, 0.9);
}

/*
 * A nav row is a legend slot: nothing until you touch it, then a shallow
 * recess. The selected one stays recessed and lit, because on hardware the
 * thing you have chosen is the thing that is pressed in.
 */
.nav-slot {
  border-radius: var(--radius);
  transition: background 0.1s ease, box-shadow 0.1s ease, color 0.1s ease;
}

.nav-slot:hover {
  background: rgba(0, 0, 0, 0.3);
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.6);
}

.nav-slot-on {
  background:
    linear-gradient(180deg,
      color-mix(in oklab, var(--color-accent1) 11%, #050a10) 0%,
      color-mix(in oklab, var(--color-accent1) 5%, #050a10) 100%);
  box-shadow:
    inset 0 1px 3px rgba(0, 0, 0, 0.8),
    inset 0 -1px 0 rgba(255, 255, 255, 0.05);
}
</style>
