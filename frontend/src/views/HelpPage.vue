<script setup lang="ts">
/**
 * The FAQ.
 *
 * Written as one long searchable document rather than a support site with
 * a page per answer, because the question people actually have is usually
 * half-formed - they know a word, not a category. So the search matches
 * both questions and answers, the sections stay visible while you filter,
 * and nothing is more than one scroll away.
 *
 * Every answer is open by default when you search and collapsed when you
 * are browsing. Hiding an answer behind a click is fine when you are
 * scanning a list of questions; it is infuriating when you have just
 * searched for the exact words that are inside it.
 */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../components/shared/PageFrame.vue'

const { t, tm, rt } = useI18n()

type Item = { q: string; a: string }
type Section = { name: string; items: Item[] }

/** The copy lives in the locale files, so this reads it rather than owning
 *  it - `tm` returns the raw message tree and `rt` resolves each leaf. */
const sections = computed<Section[]>(() =>
  (tm('faq.sections') as unknown[]).map((raw) => {
    const section = raw as { name: unknown; items: unknown[] }
    return {
      name: rt(section.name as never),
      items: section.items.map((entry) => {
        const item = entry as { q: unknown; a: unknown }
        return { q: rt(item.q as never), a: rt(item.a as never) }
      }),
    }
  }),
)

const total = computed(() =>
  sections.value.reduce((sum, section) => sum + section.items.length, 0),
)

const query = ref('')
const open = ref<Set<string>>(new Set())

const filtered = computed<Section[]>(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return sections.value
  return sections.value
    .map((section) => ({
      name: section.name,
      items: section.items.filter(
        (item) =>
          item.q.toLowerCase().includes(needle) || item.a.toLowerCase().includes(needle),
      ),
    }))
    .filter((section) => section.items.length > 0)
})

const shown = computed(() =>
  filtered.value.reduce((sum, section) => sum + section.items.length, 0),
)

function key(section: string, q: string): string {
  return `${section}::${q}`
}

function isOpen(section: string, q: string): boolean {
  // Searching opens everything it found: you have already told the program
  // what you are looking for, and making you click again to read it is the
  // search pretending it did not understand.
  return Boolean(query.value.trim()) || open.value.has(key(section, q))
}

function toggle(section: string, q: string) {
  const id = key(section, q)
  const next = new Set(open.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  open.value = next
}
</script>

<template>
  <PageFrame :title="t('faq.title')" :subtitle="t('faq.subtitle')" accent="var(--color-accent2)">
    <template #actions>
      <input
        v-model="query"
        type="search"
        :placeholder="t('faq.search')"
        class="well w-64 px-2 py-1 text-[11px] text-text outline-none"
      />
      <span v-if="query.trim()" class="readout-dim shrink-0 text-[10px]">
        {{ t('faq.resultCount', { n: shown, total }) }}
      </span>
    </template>

    <div class="max-w-4xl space-y-2">
      <section v-for="section in filtered" :key="section.name" class="card">
        <header class="rack-strip px-2.5 py-1.5">
          <h2 class="engraved text-[10px] font-semibold uppercase text-accent2">
            {{ section.name }}
          </h2>
        </header>

        <div
          v-for="item in section.items"
          :key="item.q"
          class="border-b border-black/60 last:border-0"
        >
          <button
            type="button"
            class="flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-accent1/10"
            @click="toggle(section.name, item.q)"
          >
            <span
              class="shrink-0 text-[9px] leading-[1.4] text-text-faint transition-transform"
              :style="{ transform: isOpen(section.name, item.q) ? 'none' : 'rotate(-90deg)' }"
            >▼</span>
            <span class="text-[12px] font-medium text-text">{{ item.q }}</span>
          </button>
          <p
            v-if="isOpen(section.name, item.q)"
            class="px-2.5 pb-2.5 pl-[26px] text-[12px] leading-relaxed text-text-dim"
          >{{ item.a }}</p>
        </div>
      </section>

      <p v-if="!filtered.length" class="card p-3 text-[12px] text-text-dim">
        {{ t('faq.noResults') }}
      </p>
    </div>
  </PageFrame>
</template>
