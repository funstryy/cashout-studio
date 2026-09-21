<script setup lang="ts">
/**
 * Hosting, joining, and who else is here.
 *
 * The invite is one string rather than three fields, because the way this
 * actually gets used is someone pasting it into a Discord message. Splitting
 * it into address / port / token would mean three copy buttons and three
 * chances to send the wrong one.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCollabStore } from '../../stores/collab'

const { t } = useI18n()
const collab = useCollabStore()

const displayName = ref(localStorage.getItem('cashout_collab_name') || '')
const inviteCode = ref('')
const address = ref('')
const copied = ref(false)
const draft = ref('')
const log = ref<HTMLElement | null>(null)

watch(displayName, (value) => {
  try {
    localStorage.setItem('cashout_collab_name', value)
  } catch {
    // Private mode, or storage full. The name just will not be remembered.
  }
})

// Radmin's own range, called out so it is obvious which of the machine's
// addresses is the one friends can actually reach.
const options = computed(() =>
  collab.addresses.map((a) => ({
    value: a,
    label: a.startsWith('26.') ? `${a} ${t('collab.radmin')}` : a,
  })),
)

const canHost = computed(() => displayName.value.trim().length > 0 && collab.addresses.length > 0)
const canJoin = computed(() => displayName.value.trim().length > 0 && inviteCode.value.trim().length > 0)

async function copyInvite() {
  try {
    await navigator.clipboard.writeText(collab.invite)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    copied.value = false
  }
}

function send() {
  collab.say(draft.value)
  draft.value = ''
}

// New messages are only useful if they are the ones you can see.
watch(
  () => collab.chat.length,
  async () => {
    await Promise.resolve()
    if (log.value) log.value.scrollTop = log.value.scrollHeight
  },
)

onMounted(async () => {
  await collab.refresh()
  address.value = collab.addresses[0] ?? ''
})
</script>

<template>
  <div class="space-y-3">
    <!-- Not connected -->
    <template v-if="!collab.live">
      <p class="text-xs text-text-dim">{{ t('collab.intro') }}</p>

      <input
        v-model="displayName"
        type="text"
        :placeholder="t('collab.namePlaceholder')"
        class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        maxlength="24"
      />

      <div class="grid gap-3 md:grid-cols-2">
        <div class="space-y-1.5 rounded border border-border p-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-text-faint">
            {{ t('collab.hostTitle') }}
          </p>
          <select
            v-model="address"
            class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          >
            <option v-for="option in options" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
          <p v-if="!collab.addresses.length" class="text-[10px] text-status-failed">
            {{ t('collab.noAddresses') }}
          </p>
          <button
            type="button"
            class="accent-gradient w-full rounded px-3 py-1.5 text-xs disabled:opacity-40"
            :disabled="!canHost || collab.connection === 'connecting'"
            @click="collab.host(displayName, address)"
          >{{ t('collab.startHosting') }}</button>
        </div>

        <div class="space-y-1.5 rounded border border-border p-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-text-faint">
            {{ t('collab.joinTitle') }}
          </p>
          <input
            v-model="inviteCode"
            type="text"
            :placeholder="t('collab.invitePlaceholder')"
            class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 font-mono text-[11px] text-text"
            @keyup.enter="canJoin && collab.join(inviteCode, displayName)"
          />
          <p class="text-[10px] text-text-faint">{{ t('collab.joinWarning') }}</p>
          <button
            type="button"
            class="w-full rounded border border-accent2/60 bg-accent2/10 px-3 py-1.5 text-xs text-text disabled:opacity-40"
            :disabled="!canJoin || collab.connection === 'connecting'"
            @click="collab.join(inviteCode, displayName)"
          >{{ t('collab.joinSession') }}</button>
        </div>
      </div>
    </template>

    <!-- Connected -->
    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <span class="flex items-center gap-1.5 text-xs text-text">
          <span class="h-2 w-2 animate-pulse rounded-full bg-accent1"></span>
          {{ collab.isHost ? t('collab.hostingNow') : t('collab.connectedTo', { host: collab.remote?.host }) }}
        </span>
        <button
          type="button"
          class="ml-auto rounded border border-border px-2 py-1 text-[11px] text-text-dim hover:text-status-failed"
          @click="collab.leave()"
        >{{ collab.isHost ? t('collab.endSession') : t('collab.leaveSession') }}</button>
      </div>

      <div v-if="collab.isHost && collab.invite" class="flex items-center gap-1.5">
        <code class="min-w-0 flex-1 truncate rounded bg-panel-2 px-2 py-1.5 font-mono text-[11px] text-accent1">
          {{ collab.invite }}
        </code>
        <button
          type="button"
          class="rounded border border-border px-2 py-1.5 text-[11px] text-text-dim hover:text-text"
          @click="copyInvite"
        >{{ copied ? t('collab.copied') : t('common.copy') }}</button>
      </div>

      <!-- Who is here -->
      <ul class="flex flex-wrap gap-1.5">
        <li
          v-for="peer in collab.peers"
          :key="peer.peer_id"
          class="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px]"
          :style="{ borderColor: peer.colour + '66' }"
        >
          <span class="h-2 w-2 rounded-full" :style="{ background: peer.colour }"></span>
          <span :class="peer.peer_id === collab.selfId ? 'text-text' : 'text-text-dim'">
            {{ peer.name }}{{ peer.peer_id === collab.selfId ? t('collab.you') : '' }}
          </span>
          <span v-if="peer.is_host" class="text-[9px] uppercase tracking-wide text-text-faint">
            {{ t('collab.hostTag') }}
          </span>
        </li>
      </ul>

      <!-- Chat: the thing that stops this being two people editing in silence -->
      <div ref="log" class="max-h-32 space-y-0.5 overflow-y-auto rounded bg-panel-2/40 p-2">
        <p v-if="!collab.chat.length" class="text-[10px] text-text-faint">{{ t('collab.chatEmpty') }}</p>
        <p
          v-for="line in collab.chat"
          :key="line.id"
          class="text-[11px] leading-snug"
          :class="line.system ? 'text-text-faint italic' : 'text-text-dim'"
        >
          <span v-if="!line.system" class="font-medium" :style="{ color: line.colour }">{{ line.name }}: </span>
          {{ line.text }}
        </p>
      </div>
      <div class="flex gap-1.5">
        <input
          v-model="draft"
          type="text"
          :placeholder="t('collab.chatPlaceholder')"
          class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          maxlength="500"
          @keyup.enter="send"
        />
        <button
          type="button"
          class="rounded border border-border px-2 py-1.5 text-[11px] text-text-dim hover:text-text"
          @click="send"
        >{{ t('collab.send') }}</button>
      </div>
    </template>

    <p v-if="collab.error" class="rounded bg-status-failed/10 p-2 text-[11px] text-status-failed">
      {{ collab.error }}
    </p>
  </div>
</template>
