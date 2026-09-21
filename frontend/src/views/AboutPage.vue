<script setup lang="ts">
/**
 * About.
 *
 * A mission statement is worth writing only if it says something a
 * competitor could disagree with, so this one names what the studio is
 * against rather than listing adjectives. The credits are the other half:
 * software made by people should say which people.
 *
 * The team list is data, not markup - adding a name, or a line about what
 * someone did, is one entry in the locale files and nothing here changes.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../components/shared/PageFrame.vue'

const { t, tm, rt } = useI18n()

/** Reads a list of `{ name, body }` records out of the messages. */
function pairs(path: string): { name: string; body: string }[] {
  return (tm(path) as unknown[]).map((raw) => {
    const row = raw as { name: unknown; body?: unknown; role?: unknown }
    return {
      name: rt(row.name as never),
      body: row.body === undefined && row.role === undefined
        ? ''
        : rt((row.body ?? row.role) as never),
    }
  })
}

const mission = computed(() => (tm('about.mission') as unknown[]).map((p) => rt(p as never)))
const principles = computed(() => pairs('about.principles'))
const team = computed(() => pairs('about.team'))
const stack = computed(() => pairs('about.stack'))
</script>

<template>
  <PageFrame :title="t('about.title')" :subtitle="t('about.subtitle')" accent="#a78bfa">
    <div class="grid max-w-5xl gap-2 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div class="space-y-2">
        <!-- Mission -->
        <section class="card">
          <header class="rack-strip px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase" style="color: #a78bfa">
              {{ t('about.missionTitle') }}
            </h2>
          </header>
          <div class="space-y-2 p-3">
            <p
              v-for="(paragraph, i) in mission"
              :key="i"
              class="text-[12px] leading-relaxed text-text-dim"
            >{{ paragraph }}</p>
          </div>
        </section>

        <!-- Principles -->
        <section class="card">
          <header class="rack-strip px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-accent1">
              {{ t('about.principlesTitle') }}
            </h2>
          </header>
          <div class="divide-y divide-black/60">
            <div v-for="rule in principles" :key="rule.name" class="p-2.5">
              <p class="text-[12px] font-medium text-text">{{ rule.name }}</p>
              <p class="mt-0.5 text-[12px] leading-relaxed text-text-dim">{{ rule.body }}</p>
            </div>
          </div>
        </section>

        <!-- Licence -->
        <section class="card">
          <header class="rack-strip px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-text-dim">
              {{ t('about.licenceTitle') }}
            </h2>
          </header>
          <p class="p-2.5 text-[12px] leading-relaxed text-text-dim">{{ t('about.licence') }}</p>
        </section>
      </div>

      <div class="space-y-2">
        <!-- Credits. A maker's plate, which is where a name belongs on a
             piece of equipment. -->
        <section class="card self-start">
          <header class="rack-strip px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-accent1">
              {{ t('about.teamTitle') }}
            </h2>
          </header>
          <ul class="divide-y divide-black/60">
            <li v-for="person in team" :key="person.name" class="px-2.5 py-1.5">
              <p class="text-[12px] text-text">{{ person.name }}</p>
              <p v-if="person.body" class="text-[10px] text-text-faint">{{ person.body }}</p>
            </li>
            <li class="px-2.5 py-1.5 text-[12px] text-text-dim">{{ t('about.teamRest') }}</li>
          </ul>
          <div class="divider-engraved flex items-center gap-1.5 px-2.5 py-1.5">
            <span class="led h-[5px] w-[5px]" style="background: var(--color-accent1); color: var(--color-accent1)"></span>
            <span class="engraved text-[10px] uppercase text-text-dim">{{ t('about.place') }}</span>
          </div>
        </section>

        <!-- Stack -->
        <section class="card self-start">
          <header class="rack-strip px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-accent2">
              {{ t('about.stackTitle') }}
            </h2>
          </header>
          <div class="divide-y divide-black/60">
            <div v-for="part in stack" :key="part.name" class="px-2.5 py-1.5">
              <p class="engraved text-[9px] uppercase text-text-faint">{{ part.name }}</p>
              <p class="mt-0.5 text-[11px] leading-relaxed text-text-dim">{{ part.body }}</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  </PageFrame>
</template>
