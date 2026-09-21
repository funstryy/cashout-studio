<script setup lang="ts">
/**
 * The menu bar.
 *
 * Its absence was one of the loudest signals that this was a web page
 * rather than an application. Every workstation has one, in the same place,
 * with roughly the same contents, and people reach for it before they look
 * for anything else - not because it is the fastest route to a command, but
 * because it is the complete list of them. A toolbar shows you what someone
 * decided was important; a menu bar shows you what the program can do.
 *
 * It is deliberately not a router: every item maps to something the editor
 * already does, and this component only names them and reports which one
 * was picked. Anything that needs new behaviour does not belong here yet.
 *
 * Disabled items stay visible. Greying out Undo teaches you where Undo is;
 * hiding it teaches you nothing and makes the menu move under the cursor.
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  canUndo: boolean
  canRedo: boolean
  playing: boolean
  loopEnabled: boolean
  metronome: boolean
  browserOpen: boolean
  toolsOpen: boolean
  dockTab: 'rack' | 'mixer' | null
  dirty: boolean
}>()

const emit = defineEmits<{ action: [id: string] }>()

const { t } = useI18n()

type Item =
  | { kind: 'separator' }
  | {
      kind?: 'item'
      id: string
      label: string
      /** Printed on the right. Not bound here - the editor owns the real
       *  key handling, and a menu that claims a shortcut it does not own is
       *  worse than one that claims none. */
      keys?: string
      disabled?: boolean
      /** Shown with a tick, for the things that are on or off. */
      checked?: boolean
    }

const menus = () => [
  {
    id: 'file',
    label: t('menu.file'),
    items: [
      { id: 'file.new', label: t('menu.newProject'), keys: 'Ctrl+N' },
      { id: 'file.open', label: t('menu.openProject'), keys: 'Ctrl+O' },
      { kind: 'separator' as const },
      { id: 'file.save', label: t('menu.save'), keys: 'Ctrl+S', disabled: !props.dirty },
      { id: 'file.export', label: t('menu.export'), keys: 'Ctrl+E' },
      { kind: 'separator' as const },
      { id: 'file.close', label: t('menu.close') },
    ] as Item[],
  },
  {
    id: 'edit',
    label: t('menu.edit'),
    items: [
      { id: 'edit.undo', label: t('editor.undo'), keys: 'Ctrl+Z', disabled: !props.canUndo },
      { id: 'edit.redo', label: t('editor.redo'), keys: 'Ctrl+Y', disabled: !props.canRedo },
      { kind: 'separator' as const },
      { id: 'edit.split', label: t('editor.split'), keys: 'S' },
      { id: 'edit.addTrack', label: t('editor.addTrack') },
    ] as Item[],
  },
  {
    id: 'view',
    label: t('menu.view'),
    items: [
      { id: 'view.playlist', label: t('editor.playlist'), keys: 'F5' },
      { id: 'view.rack', label: t('rack.title'), keys: 'F6', checked: props.dockTab === 'rack' },
      { id: 'view.pianoRoll', label: t('pianoRoll.title'), keys: 'F7' },
      { id: 'view.browser', label: t('browser.title'), keys: 'F8', checked: props.browserOpen },
      { id: 'view.mixer', label: t('dawMixer.title'), keys: 'F9', checked: props.dockTab === 'mixer' },
      { kind: 'separator' as const },
      { id: 'view.tools', label: t('editor.toolsRail'), checked: props.toolsOpen },
    ] as Item[],
  },
  {
    id: 'transport',
    label: t('menu.transport'),
    items: [
      { id: 'transport.play', label: props.playing ? t('editor.pause') : t('editor.play'), keys: 'Space' },
      { id: 'transport.stop', label: t('editor.stop') },
      { id: 'transport.record', label: t('editor.record'), keys: 'R' },
      { kind: 'separator' as const },
      { id: 'transport.loop', label: t('editor.loop'), keys: 'L', checked: props.loopEnabled },
      { id: 'transport.metronome', label: t('editor.click'), checked: props.metronome },
    ] as Item[],
  },
  {
    id: 'help',
    label: t('menu.help'),
    items: [
      { id: 'help.shortcuts', label: t('menu.shortcuts') },
      { id: 'help.faq', label: t('faq.title') },
      { id: 'help.about', label: t('about.title') },
    ] as Item[],
  },
]

const open = ref<string | null>(null)

/** Hovering moves between menus once one is open, the way a real menu bar
 *  behaves - you do not have to click each title in turn. */
function onEnter(id: string) {
  if (open.value !== null) open.value = id
}

function choose(item: Item) {
  if (item.kind === 'separator' || item.disabled) return
  open.value = null
  emit('action', item.id)
}

function onDocumentPointerDown(event: PointerEvent) {
  if (!(event.target as HTMLElement).closest('[data-menubar]')) open.value = null
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = null
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div data-menubar class="rack-strip relative z-40 flex h-6 items-center gap-0 px-1">
    <div v-for="menu in menus()" :key="menu.id" class="relative">
      <button
        type="button"
        class="h-[22px] rounded-[1px] px-2 text-[11px] transition-colors"
        :class="open === menu.id ? 'bg-accent1/20 text-text' : 'text-text-dim hover:text-text'"
        @click="open = open === menu.id ? null : menu.id"
        @pointerenter="onEnter(menu.id)"
      >{{ menu.label }}</button>

      <div
        v-if="open === menu.id"
        class="card absolute left-0 top-[23px] min-w-[220px] py-1 shadow-2xl"
      >
        <template v-for="(item, i) in menu.items" :key="i">
          <div v-if="item.kind === 'separator'" class="divider-engraved my-1"></div>
          <button
            v-else
            type="button"
            class="flex w-full items-center gap-3 px-2.5 py-[3px] text-left text-[11px] transition-colors"
            :class="item.disabled
              ? 'cursor-default text-text-faint/50'
              : 'text-text-dim hover:bg-accent1/15 hover:text-text'"
            @click="choose(item)"
          >
            <span class="w-3 shrink-0 text-accent1">{{ item.checked ? '✓' : '' }}</span>
            <span class="flex-1 truncate">{{ item.label }}</span>
            <span v-if="item.keys" class="shrink-0 text-[10px] tabular-nums text-text-faint">
              {{ item.keys }}
            </span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
