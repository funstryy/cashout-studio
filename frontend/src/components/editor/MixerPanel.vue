<script setup lang="ts">
/**
 * The mixer, laid out the way FL Studio's is.
 *
 * A row of vertical strips - master first, then one per track - and an
 * insert list for whichever strip is selected. That shape is not nostalgia:
 * a horizontal row of faders is how you compare levels at a glance, and
 * putting the effect slots in one shared column instead of on every strip is
 * what keeps thirty tracks on screen at once.
 *
 * The insert list shows what has actually been printed onto the track, which
 * is the honest version of an effect slot here: a VST is native code and the
 * timeline plays in the browser, so an insert is a render rather than a live
 * node. Clicking a slot reopens that plugin with the settings it was printed
 * with; the empty slot adds a new one.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { lanePlugins } from '../../audio/timelineTypes'
import type { TimelineLane } from '../../audio/timelineTypes'
import type { ChannelSettings, MasterSettings } from '../../audio/mixerEngine'

const props = defineProps<{
  lanes: TimelineLane[]
  master: MasterSettings
  laneLevels: { peak: number; clipping: boolean }[]
  masterLevel: { peak: number; clipping: boolean }
  selected: number | null
}>()

const emit = defineEmits<{
  select: [index: number | null]
  updateLane: [payload: { index: number; settings: ChannelSettings }]
  updateMaster: [settings: MasterSettings]
  openPlugins: [payload: { laneIndex: number; pluginPath?: string }]
}>()

const { t } = useI18n()

/** Six slots, like FL. Enough to read as a rack, few enough to fit. */
const SLOTS = 6

const selectedLane = computed(() =>
  props.selected == null ? null : props.lanes[props.selected] ?? null,
)

const slots = computed(() => {
  const applied = selectedLane.value ? lanePlugins(selectedLane.value) : []
  return Array.from({ length: Math.max(SLOTS, applied.length + 1) }, (_, index) => applied[index] ?? null)
})

/**
 * Fader position to gain.
 *
 * Deliberately not linear. A linear fader spends most of its travel in the
 * top 6 dB, where nothing useful happens, and crams every quiet setting into
 * the bottom centimetre. Squaring it puts unity around three-quarters up,
 * which is where a hand expects to find it.
 */
function faderToGain(position: number): number {
  return Math.pow(position, 2) * 1.5
}
function gainToFader(gain: number): number {
  return Math.sqrt(Math.max(0, Math.min(1.5, gain)) / 1.5)
}

function dbLabel(gain: number): string {
  if (gain <= 0.0001) return '−∞'
  const db = 20 * Math.log10(gain)
  return `${db > 0 ? '+' : ''}${db.toFixed(1)}`
}

function setLaneVolume(index: number, position: number) {
  const lane = props.lanes[index]
  if (!lane) return
  emit('updateLane', { index, settings: { ...lane.settings, volume: faderToGain(position) } })
}

function setLanePan(index: number, pan: number) {
  const lane = props.lanes[index]
  if (!lane) return
  emit('updateLane', { index, settings: { ...lane.settings, pan } })
}

function toggle(index: number, key: 'muted' | 'solo') {
  const lane = props.lanes[index]
  if (!lane) return
  emit('updateLane', { index, settings: { ...lane.settings, [key]: !lane.settings[key] } })
}

function setMasterVolume(position: number) {
  emit('updateMaster', { ...props.master, volume: faderToGain(position) })
}

/** Meter height as a percentage, on a curve that makes quiet signal visible.
 *  A linear meter looks dead until something is nearly clipping. */
function meterHeight(peak: number): number {
  return Math.min(100, Math.pow(Math.max(0, peak), 0.6) * 100)
}
</script>

<template>
  <section class="overflow-hidden">
    <div class="flex">
      <!-- Strips -->
      <div class="flex min-w-0 flex-1 gap-px overflow-x-auto bg-border/40 p-px">
        <!-- Master, pinned first the way FL pins it -->
        <div
          class="flex w-[58px] shrink-0 flex-col items-center gap-1 px-1 py-2 transition-colors"
          :class="selected == null ? 'bg-panel-3' : 'bg-panel hover:bg-panel-2'"
          @click="emit('select', null)"
        >
          <span class="text-[10px] font-semibold text-accent1">{{ t('dawMixer.master') }}</span>
          <div class="flex flex-1 items-end gap-1">
            <input
              :value="gainToFader(master.volume)"
              type="range" min="0" max="1" step="0.005"
              class="mixer-fader"
              :title="dbLabel(master.volume) + ' dB'"
              @input="setMasterVolume(Number(($event.target as HTMLInputElement).value))"
            />
            <div class="relative h-24 w-2 overflow-hidden rounded-sm bg-panel-2">
              <div
                class="absolute bottom-0 w-full transition-[height] duration-75"
                :class="masterLevel.clipping ? 'bg-status-failed' : 'bg-gradient-to-t from-accent1 to-accent2'"
                :style="{ height: `${meterHeight(masterLevel.peak)}%` }"
              ></div>
            </div>
          </div>
          <span class="text-[9px] tabular-nums text-text-faint">{{ dbLabel(master.volume) }}</span>
        </div>

        <!-- One per track -->
        <div
          v-for="(lane, index) in lanes"
          :key="lane.id"
          class="flex w-[58px] shrink-0 cursor-pointer flex-col items-center gap-1 px-1 py-2 transition-colors"
          :class="selected === index ? 'bg-panel-3' : 'bg-panel hover:bg-panel-2'"
          @click="emit('select', index)"
        >
          <span class="w-full truncate text-center text-[9px] text-text-dim" :title="lane.name">
            {{ lane.name }}
          </span>

          <!-- Pan: a slim horizontal strip rather than a knob. A knob drawn
               at this size is a dot you cannot aim at. -->
          <input
            :value="lane.settings.pan"
            type="range" min="-1" max="1" step="0.02"
            class="mixer-pan"
            :title="`${t('dawMixer.pan')} ${lane.settings.pan.toFixed(2)}`"
            @click.stop
            @input="setLanePan(index, Number(($event.target as HTMLInputElement).value))"
          />

          <div class="flex flex-1 items-end gap-1">
            <input
              :value="gainToFader(lane.settings.volume)"
              type="range" min="0" max="1" step="0.005"
              class="mixer-fader"
              :title="dbLabel(lane.settings.volume) + ' dB'"
              @click.stop
              @input="setLaneVolume(index, Number(($event.target as HTMLInputElement).value))"
            />
            <div class="relative h-24 w-2 overflow-hidden rounded-sm bg-panel-2">
              <div
                class="absolute bottom-0 w-full transition-[height] duration-75"
                :class="laneLevels[index]?.clipping ? 'bg-status-failed' : 'bg-gradient-to-t from-accent1 to-accent2'"
                :style="{ height: `${meterHeight(laneLevels[index]?.peak ?? 0)}%` }"
              ></div>
            </div>
          </div>

          <div class="flex gap-0.5">
            <button
              type="button"
              class="rounded px-1 text-[9px] font-semibold transition-colors"
              :class="lane.settings.muted ? 'bg-status-failed/30 text-status-failed' : 'text-text-faint hover:text-text'"
              @click.stop="toggle(index, 'muted')"
            >M</button>
            <button
              type="button"
              class="rounded px-1 text-[9px] font-semibold transition-colors"
              :class="lane.settings.solo ? 'bg-accent1/30 text-accent1' : 'text-text-faint hover:text-text'"
              @click.stop="toggle(index, 'solo')"
            >S</button>
          </div>
          <span class="text-[9px] tabular-nums text-text-faint">{{ dbLabel(lane.settings.volume) }}</span>
        </div>
      </div>

      <!-- Insert rack for the selected strip -->
      <div class="w-56 shrink-0 border-l border-border bg-panel-2/40 p-2">
        <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-text-faint">
          {{ t('dawMixer.inserts') }}
        </p>

        <p v-if="selected == null" class="text-[10px] leading-snug text-text-faint">
          {{ t('dawMixer.masterNoInserts') }}
        </p>

        <ul v-else class="space-y-0.5">
          <li v-for="(slot, index) in slots" :key="index">
            <button
              type="button"
              class="flex w-full items-center gap-1.5 rounded border px-1.5 py-1 text-left text-[10px] transition-colors"
              :class="slot
                ? 'border-accent1/30 bg-accent1/10 text-text hover:border-accent1/60'
                : 'border-dashed border-border text-text-faint hover:border-accent1/40 hover:text-text-dim'"
              @click="emit('openPlugins', { laneIndex: selected!, pluginPath: slot?.path })"
            >
              <span class="w-3 shrink-0 tabular-nums opacity-50">{{ index + 1 }}</span>
              <span class="truncate">{{ slot ? slot.name : t('dawMixer.emptySlot') }}</span>
            </button>
          </li>
        </ul>

        <p v-if="selected != null" class="mt-2 text-[9px] leading-snug text-text-faint">
          {{ t('dawMixer.printedNote') }}
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
/*
 * Vertical faders. `writing-mode: vertical-lr` with direction reversed is
 * the one way to get a native range input to travel upwards and still be
 * draggable and keyboard-accessible - a rotated element keeps its original
 * hit box and ends up impossible to grab.
 */
.mixer-fader {
  writing-mode: vertical-lr;
  direction: rtl;
  width: 22px;
  height: 104px;
  padding: 0;
  /*
   * The throw is a slot cut down the middle of a wider landing, not a
   * coloured pill. Drawn with a gradient rather than a child element so the
   * input keeps its own hit box: the whole 22px is grabbable, which is what
   * makes a fader comfortable, while only the middle 6px looks cut.
   */
  background:
    linear-gradient(90deg,
      transparent 0 8px,
      #000 8px 9px,
      #03070c 9px 13px,
      rgba(255, 255, 255, 0.05) 13px 14px,
      transparent 14px 100%);
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

/*
 * The cap has to turn with the fader. The thumb's width and height stay
 * physical under `writing-mode: vertical-lr`, so a cap authored for a
 * horizontal fader arrives on a vertical one standing on its end - which
 * is what it was doing: tall and narrow on a control that travels
 * vertically, when the whole point of the shape is that it is wide across
 * the throw and thin along it.
 */
.mixer-fader::-webkit-slider-thumb {
  width: 20px;
  height: 11px;
  background: linear-gradient(180deg,
    #46566b 0%, #2c3947 42%,
    #0b1117 43%, #0b1117 57%,
    #263140 58%, #131b25 100%);
}

.mixer-pan {
  width: 46px;
  height: 5px;
}

/* Pan is a trim, not a fader, so its cap is small and square-ish - the
   same distinction a desk makes between a throw you ride and a control you
   set once. */
.mixer-pan::-webkit-slider-thumb {
  width: 9px;
  height: 13px;
}
</style>
