/**
 * FL Studio's keyboard, as far as it maps onto this app.
 *
 * Muscle memory is the whole argument. Someone coming from FL already knows
 * that F5 is the playlist and C is the slice tool, and a DAW that invents
 * its own bindings makes them stop and look every single time. Where a
 * binding has no equivalent here it is simply left out rather than
 * repurposed - a familiar key doing an unfamiliar thing is worse than a key
 * that does nothing.
 *
 * Tools (single letters, as in FL's toolbar):
 *   P  draw        E  select       C  slice
 *   D  delete      M  mute         T  playback
 *
 * Panels:
 *   F5 playlist    F6 channel rack    F7 piano roll    F9 mixer
 *
 * Transport and editing:
 *   Space play/pause      R record           L loop
 *   Ctrl+S save           Ctrl+Z / Ctrl+Y undo, redo
 *   Ctrl+B duplicate      Ctrl+C / Ctrl+V    Delete remove
 *
 * Nothing fires while a text field has focus. Typing a track name called
 * "Crash" should not delete the clip, slice the next one and toggle the
 * mixer on the way past.
 */
import { onBeforeUnmount, onMounted } from 'vue'

export type DawTool = 'draw' | 'select' | 'slice' | 'delete' | 'mute' | 'playback'

export interface DawShortcutHandlers {
  setTool: (tool: DawTool) => void
  playPause: () => void
  stop: () => void
  record: () => void
  toggleLoop: () => void
  save: () => void
  undo: () => void
  redo: () => void
  duplicate: () => void
  remove: () => void
  copy: () => void
  paste: () => void
  splitAtPlayhead: () => void
  togglePanel: (panel: 'playlist' | 'rack' | 'pianoRoll' | 'mixer' | 'browser') => void
}

const TOOL_KEYS: Record<string, DawTool> = {
  p: 'draw',
  e: 'select',
  c: 'slice',
  d: 'delete',
  m: 'mute',
  t: 'playback',
}

function typingInto(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tag = element.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || element.isContentEditable
}

export function useDawShortcuts(handlers: DawShortcutHandlers): void {
  function onKeydown(event: KeyboardEvent) {
    if (typingInto(event.target)) return

    const control = event.ctrlKey || event.metaKey
    const key = event.key.toLowerCase()

    // ---- panels ----------------------------------------------------------
    const panels: Record<string, Parameters<DawShortcutHandlers['togglePanel']>[0]> = {
      f5: 'playlist',
      f6: 'rack',
      f7: 'pianoRoll',
      f8: 'browser',
      f9: 'mixer',
    }
    if (panels[key]) {
      event.preventDefault()
      handlers.togglePanel(panels[key])
      return
    }

    // ---- chorded ---------------------------------------------------------
    if (control) {
      switch (key) {
        case 's':
          event.preventDefault()
          handlers.save()
          return
        case 'z':
          event.preventDefault()
          // Ctrl+Shift+Z is the other redo people reach for.
          if (event.shiftKey) handlers.redo()
          else handlers.undo()
          return
        case 'y':
          event.preventDefault()
          handlers.redo()
          return
        case 'b':
          event.preventDefault()
          handlers.duplicate()
          return
        case 'c':
          event.preventDefault()
          handlers.copy()
          return
        case 'v':
          event.preventDefault()
          handlers.paste()
          return
        default:
          return
      }
    }

    if (event.altKey || event.shiftKey) return

    // ---- single keys -----------------------------------------------------
    if (key === ' ') {
      event.preventDefault()
      handlers.playPause()
      return
    }
    if (event.key === 'Escape') {
      handlers.stop()
      return
    }
    if (key === 'delete' || key === 'backspace') {
      event.preventDefault()
      handlers.remove()
      return
    }
    // FL has no single-key split; the slice tool does it by clicking. This
    // adds the one a keyboard-driven edit actually wants: cut at the
    // playhead, the way S does in most other DAWs.
    if (key === 's') {
      event.preventDefault()
      handlers.splitAtPlayhead()
      return
    }
    if (key === 'r') {
      event.preventDefault()
      handlers.record()
      return
    }
    if (key === 'l') {
      event.preventDefault()
      handlers.toggleLoop()
      return
    }
    if (TOOL_KEYS[key]) {
      event.preventDefault()
      handlers.setTool(TOOL_KEYS[key])
    }
  }

  onMounted(() => window.addEventListener('keydown', onKeydown))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
}
