<script setup lang="ts">
/**
 * The way into the DAW.
 *
 * Three doors, not one. A solo project is still the common case, but a
 * multiplayer project used to mean opening a solo one and hunting for a
 * collapsed panel, and a friend arriving with an invite code had nowhere at
 * all to paste it - they had to create a project they did not want first.
 * Both of those belong on the screen you land on.
 *
 * Nothing here is a different kind of project. A solo project can go
 * multiplayer at any point from the transport's session button, so this
 * picks a starting point rather than committing you to anything.
 */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import { useRouter } from 'vue-router'
import * as projectsApi from '../../api/projects'
import type { ProjectSummary } from '../../api/projects'
import { parseInvite } from '../../api/collab'

const { t, locale } = useI18n()
const router = useRouter()

const projects = ref<ProjectSummary[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const inviteCode = ref('')
const inviteError = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    projects.value = await projectsApi.listProjects()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function remove(id: number): Promise<void> {
  if (!window.confirm(t('projectsPage.confirmDelete'))) return
  await projectsApi.deleteProject(id)
  await load()
}

/** Checked here rather than after the jump: a typo should be caught on the
 *  screen you typed it on, not by an empty editor two navigations later. */
function join(): void {
  const code = inviteCode.value.trim()
  if (!parseInvite(code)) {
    inviteError.value = t('projectsPage.badInvite')
    return
  }
  inviteError.value = ''
  void router.push({ path: '/editor/new', query: { join: code } })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(locale.value === 'ru' ? 'ru-RU' : 'en-US')
}

onMounted(load)
</script>

<template>
  <PageFrame :title="t('projectsPage.title')">
    <div class="max-w-3xl space-y-3">

    <!-- Start something -->
    <div class="grid gap-3 sm:grid-cols-2">
      <router-link
        to="/editor/new"
        class="card group flex flex-col gap-1 p-4 transition-colors hover:border-accent1/50"
      >
        <span class="text-sm font-medium text-text">{{ t('projectsPage.soloTitle') }}</span>
        <span class="text-xs text-text-dim">{{ t('projectsPage.soloBlurb') }}</span>
        <span class="mt-2 text-xs text-accent1 opacity-0 transition-opacity group-hover:opacity-100">
          {{ t('projectsPage.open') }} →
        </span>
      </router-link>

      <router-link
        :to="{ path: '/editor/new', query: { host: '1' } }"
        class="card group flex flex-col gap-1 p-4 transition-colors hover:border-accent2/50"
      >
        <span class="flex items-center gap-1.5 text-sm font-medium text-text">
          <span class="h-1.5 w-1.5 rounded-full bg-accent2"></span>
          {{ t('projectsPage.multiTitle') }}
        </span>
        <span class="text-xs text-text-dim">{{ t('projectsPage.multiBlurb') }}</span>
        <span class="mt-2 text-xs text-accent2 opacity-0 transition-opacity group-hover:opacity-100">
          {{ t('projectsPage.open') }} →
        </span>
      </router-link>
    </div>

    <!-- Someone sent you a code -->
    <div class="rounded-xl border border-border p-3">
      <p class="mb-2 text-xs text-text-dim">{{ t('projectsPage.joinBlurb') }}</p>
      <div class="flex flex-wrap gap-1.5">
        <input
          v-model="inviteCode"
          type="text"
          :placeholder="t('collab.invitePlaceholder')"
          class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-2 py-1.5 font-mono text-[11px] text-text"
          @keyup.enter="join"
        />
        <button
          type="button"
          class="rounded border border-accent2/60 bg-accent2/10 px-3 py-1.5 text-xs text-text disabled:opacity-40"
          :disabled="!inviteCode.trim()"
          @click="join"
        >{{ t('collab.joinSession') }}</button>
      </div>
      <p v-if="inviteError" class="mt-1.5 text-[11px] text-status-failed">{{ inviteError }}</p>
    </div>

    <!-- What you already have -->
    <div class="space-y-2">
      <p class="text-[10px] font-semibold uppercase tracking-wider text-text-faint">
        {{ t('projectsPage.saved') }}
      </p>

      <p v-if="loading" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
      <p v-else-if="error" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ error }}</p>
      <p
        v-else-if="projects.length === 0"
        class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim"
      >
        {{ t('projectsPage.empty') }}
      </p>

      <div v-else class="space-y-2">
        <div
          v-for="p in projects"
          :key="p.id"
          class="flex items-center justify-between rounded-xl border border-border bg-panel p-3"
        >
          <router-link :to="`/editor/${p.id}`" class="min-w-0">
            <p class="truncate text-sm font-medium text-text">{{ p.name }}</p>
            <p class="text-xs text-text-dim">{{ t('projectsPage.modified', { date: formatDate(p.updated_at) }) }}</p>
          </router-link>
          <button
            type="button"
            class="shrink-0 text-text-dim hover:text-status-failed"
            :title="t('common.delete')"
            @click="remove(p.id)"
          >✕</button>
        </div>
      </div>
    </div>
    </div>
  </PageFrame>
</template>
