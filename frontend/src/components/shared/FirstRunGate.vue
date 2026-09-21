<script setup lang="ts">
/**
 * First-run gate: licence acceptance, then choosing a profile.
 *
 * Both are local. Acceptance is remembered in this browser profile's storage
 * and the chosen profile is a row in the local database - there is no server
 * to check with, because the machine is the server.
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as usersApi from '../../api/users'
import type { UserProfile } from '../../api/users'

const emit = defineEmits<{ ready: [] }>()
const { t } = useI18n()

const EULA_KEY = 'cashout_studio_eula_accepted_v1'

const step = ref<'licence' | 'profile' | 'done'>('licence')
const profiles = ref<UserProfile[]>([])
const newName = ref('')
const busy = ref(false)
const error = ref('')

const canCreate = computed(() => newName.value.trim().length > 0 && !busy.value)

function licenceAccepted(): boolean {
  try {
    return localStorage.getItem(EULA_KEY) === 'yes'
  } catch {
    return false
  }
}

async function loadProfiles(): Promise<void> {
  try {
    const result = await usersApi.listUsers()
    profiles.value = result.users
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function acceptLicence(): void {
  try {
    localStorage.setItem(EULA_KEY, 'yes')
  } catch {
    // Not fatal - it will simply ask again next launch.
  }
  step.value = 'profile'
  void loadProfiles()
}

function choose(profile: UserProfile): void {
  usersApi.setCurrentUserId(profile.id)
  step.value = 'done'
  emit('ready')
}

async function create(): Promise<void> {
  if (!canCreate.value) return
  busy.value = true
  error.value = ''
  try {
    choose(await usersApi.createUser(newName.value.trim()))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  const chosen = usersApi.currentUserId()
  if (licenceAccepted() && chosen != null) {
    step.value = 'done'
    emit('ready')
    return
  }
  if (licenceAccepted()) {
    step.value = 'profile'
    await loadProfiles()
  }
})
</script>

<template>
  <div v-if="step !== 'done'" class="fixed inset-0 z-[100] flex items-center justify-center bg-bg p-4">
    <div class="w-full max-w-2xl rounded-xl border border-border bg-panel p-6">
      <div class="flex items-center gap-3">
        <img src="/cashout-studio-logo.svg" alt="" class="h-9 w-9 rounded-md" />
        <div>
          <h1 class="text-lg font-semibold text-text">Cashout Studio</h1>
          <p class="text-xs text-text-dim">{{ t('header.tagline') }}</p>
        </div>
      </div>

      <template v-if="step === 'licence'">
        <h2 class="mt-5 text-sm font-semibold text-text">{{ t('firstRun.licenceTitle') }}</h2>
        <div class="mt-2 max-h-72 overflow-y-auto rounded-lg border border-border bg-panel-2 p-3 text-xs leading-relaxed text-text-dim">
          <p>{{ t('firstRun.licenceIntro') }}</p>
          <p class="mt-2">{{ t('firstRun.licenceGrant') }}</p>
          <p class="mt-2">{{ t('firstRun.licenceWarranty') }}</p>
          <h3 class="mt-3 font-semibold text-text">{{ t('firstRun.thirdPartyTitle') }}</h3>
          <p >{{ t('firstRun.thirdPartyBody') }}</p>
          <h3 class="mt-3 font-semibold text-text">{{ t('firstRun.contentTitle') }}</h3>
          <p >{{ t('firstRun.contentBody') }}</p>
        </div>
        <button type="button" class="accent-gradient mt-4 rounded-lg px-5 py-2 text-sm font-medium text-white" @click="acceptLicence">
          {{ t('firstRun.accept') }}
        </button>
      </template>

      <template v-else>
        <h2 class="mt-5 text-sm font-semibold text-text">{{ t('firstRun.profileTitle') }}</h2>
        <p class="mt-1 text-xs text-text-dim">{{ t('firstRun.profileHint') }}</p>

        <ul v-if="profiles.length" class="mt-3 space-y-1">
          <li v-for="profile in profiles" :key="profile.id">
            <button
              type="button"
              class="w-full rounded-lg border border-border px-3 py-2 text-left text-sm text-text hover:border-accent1/60 hover:bg-panel-2"
              @click="choose(profile)"
            >
              {{ profile.name }}
            </button>
          </li>
        </ul>

        <div class="mt-4 flex gap-2">
          <input
            v-model="newName"
            type="text"
            :placeholder="t('firstRun.newProfilePlaceholder')"
            class="flex-1 rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
            @keyup.enter="create"
          />
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm text-text disabled:opacity-50"
            :disabled="!canCreate"
            @click="create"
          >
            {{ t('firstRun.createProfile') }}
          </button>
        </div>
        <p v-if="error" class="mt-2 text-xs text-status-failed">{{ error }}</p>
      </template>
    </div>
  </div>
</template>
