<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useNavigationGuard } from './composables/useNavigationGuard'
import { useOrchestratorStore } from './stores/orchestrator'
import AppSidebar from './components/shared/AppSidebar.vue'
import FirstRunGate from './components/shared/FirstRunGate.vue'
import IntroSplash from './components/shared/IntroSplash.vue'
import FirstRunTour from './components/shared/FirstRunTour.vue'

const orchestrator = useOrchestratorStore()
const { t } = useI18n()

// A desktop window has no address bar, so navigating out of the app is a
// dead end rather than a mistake you can undo. See the composable.
useNavigationGuard(useRouter())

// Nothing behind the gate is rendered until the licence is accepted and a
// profile is chosen - otherwise the first paint would fetch a history that
// belongs to nobody in particular.
const ready = ref(false)

// The splash covers everything while it plays, including the licence gate.
// It unmounts itself when done, so nothing underneath has to wait on it -
// the gate and the app render behind it and are simply revealed.
const introDone = ref(false)

watchEffect(() => {
  document.title = `Cashout Studio: ${t('header.tagline')}`
})

onMounted(() => orchestrator.startPolling())
onBeforeUnmount(() => orchestrator.stopPolling())

// Every route owns the whole window now, and every view frames itself -
// the workspace through its zones, the rest through PageFrame - so there
// is no longer a special case for the DAW here.
</script>

<template>
  <IntroSplash v-if="!introDone" @done="introDone = true" />
  <!-- Only once the intro is out of the way and a profile exists, so the
       first thing anyone reads is not a tutorial covering a licence
       agreement they have not accepted yet. -->
  <FirstRunTour v-if="introDone && ready" />
  <FirstRunGate @ready="ready = true" />
  <!-- The rail is fixed and the content scrolls beside it, rather than the
       whole page scrolling: a workstation's navigation should not slide away
       when you scroll a track list. The max-width cap is gone too - the DAW
       and the mixer want every pixel of a wide monitor. -->
  <div v-if="ready" class="flex h-svh w-full overflow-hidden">
    <AppSidebar />
    <!-- Fixed height, not a minimum. Every view now owns its own scrolling
         - the workspace through its zones, everything else through
         PageFrame - so the window itself never scrolls and a title strip
         is somewhere you can always look. A document that scrolls as a
         whole is the single most page-like thing an application can do. -->
    <main class="flex min-w-0 flex-1 flex-col overflow-hidden">
      <!-- No page transition here, deliberately. `mode="out-in"` waits for
           the leaving view's transition to finish before mounting the next
           one, and Vue drives that through requestAnimationFrame. A desktop
           WebView that is occluded or minimised stops firing rAF, so the
           leave never completes, the next page never mounts, and the app
           looks frozen with a blank body - measured: an element left stuck
           on `.fade-leave-active` with nothing entering, and every
           subsequent navigation ignored. A 150ms cross-fade is not worth a
           failure mode that eats the whole application. -->
      <router-view />
    </main>
  </div>
</template>
