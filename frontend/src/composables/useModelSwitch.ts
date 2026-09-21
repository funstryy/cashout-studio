import { useRouter } from 'vue-router'
import { useOrchestratorStore } from '../stores/orchestrator'
import type { ModelId } from '../types'

/**
 * Where clicking an engine takes you.
 *
 * YuE2 has no page of its own any more - the one server behind it now runs
 * Stable Audio, the voices and the separators, so Stable Audio is the page
 * someone starting "the audio engine" most likely wants. It used to point
 * at a route named 'yue2' that no longer exists, and the push rejected
 * before the engine was ever asked to start: the button did nothing at all.
 */
export const MODEL_ROUTES: Record<ModelId, string> = {
  ace_step: 'ace-step',
  yue2: 'stable-audio',
}

export const MODEL_LABELS: Record<ModelId, string> = {
  ace_step: 'ACE-Step 1.5',
  yue2: 'YuE2 · Audio engine',
}

/** Navigates to the model's page and (unless already running) tells the backend to switch to it. */
export function useModelSwitch() {
  const router = useRouter()
  const orchestrator = useOrchestratorStore()

  async function selectModel(id: ModelId): Promise<void> {
    const routeName = MODEL_ROUTES[id]
    if (router.currentRoute.value.name !== routeName) {
      // Best effort. Going somewhere useful is a convenience; starting the
      // engine is the whole point of the click, and a routing problem must
      // never be what stops it happening.
      try {
        await router.push({ name: routeName })
      } catch {
        // Stay where we are and start the engine anyway.
      }
    }
    const status = orchestrator.statuses[id]?.status ?? 'stopped'
    if (orchestrator.activeModel === id && status === 'running') return
    await orchestrator.switchModel(id)
  }

  return { selectModel }
}
