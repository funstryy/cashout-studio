/**
 * Stops the app navigating out of itself.
 *
 * The bug this exists for: pressing the mouse's back button froze the studio
 * and left every tab blank. Nothing had crashed - the WebView had simply
 * obeyed a back gesture past the app's first history entry, unmounted the
 * Vue app and left an empty document behind. Every click afterwards hit a
 * page that no longer had an application on it.
 *
 * In a browser tab, back leaving the site is correct. In a desktop window it
 * is a dead end with no address bar to escape from, so the gestures are
 * intercepted and routed through the router instead, and refused when they
 * would leave.
 *
 * Covers the mouse's fourth and fifth buttons, Alt+Left/Right, and the
 * Backspace-navigates-back behaviour some engines still carry.
 */
import { onBeforeUnmount, onMounted } from 'vue'
import type { Router } from 'vue-router'

/** Vue Router 4 stamps an incrementing `position` into each history entry,
 *  which is how we tell "back within the app" from "back out of it". */
function position(): number {
  const state = window.history.state as { position?: number } | null
  return typeof state?.position === 'number' ? state.position : 0
}

function isTextEntry(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tag = element.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || element.isContentEditable
}

export function useNavigationGuard(router: Router): void {
  // Captured at mount: anything at or below this is the entry the app opened
  // on, and going back from there is what produced the blank window.
  let floor = position()

  function goBack() {
    if (position() > floor) router.back()
  }

  function onMouseButton(event: MouseEvent) {
    // 3 and 4 are the thumb buttons. Chromium acts on them at mouseup, but
    // the default has to be cancelled on every phase or the navigation still
    // happens - which is why this is bound to three events in capture.
    if (event.button !== 3 && event.button !== 4) return
    event.preventDefault()
    event.stopPropagation()
    if (event.type !== 'mouseup') return
    if (event.button === 3) goBack()
    else router.forward()
  }

  function onKey(event: KeyboardEvent) {
    if (event.altKey && event.key === 'ArrowLeft') {
      event.preventDefault()
      goBack()
      return
    }
    if (event.altKey && event.key === 'ArrowRight') {
      event.preventDefault()
      router.forward()
      return
    }
    // Backspace outside a text field used to mean "back". It must never
    // mean that here - the DAW binds Backspace to deleting the selected
    // clip, and losing the whole app instead would be a spectacular misfire.
    if (event.key === 'Backspace' && !isTextEntry(event.target)) {
      event.preventDefault()
    }
  }

  onMounted(() => {
    floor = position()
    window.addEventListener('mousedown', onMouseButton, true)
    window.addEventListener('mouseup', onMouseButton, true)
    window.addEventListener('auxclick', onMouseButton, true)
    window.addEventListener('keydown', onKey, true)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('mousedown', onMouseButton, true)
    window.removeEventListener('mouseup', onMouseButton, true)
    window.removeEventListener('auxclick', onMouseButton, true)
    window.removeEventListener('keydown', onKey, true)
  })
}
