import { acceptHMRUpdate, defineStore } from 'pinia'
import * as projectsApi from '../api/projects'
import { defaultChannelSettings, defaultMasterSettings } from '../audio/mixerEngine'
import type { ChannelSettings, MasterSettings } from '../audio/mixerEngine'
import { projectDuration } from '../audio/timelineTypes'
import type { Clip, TimelineLane, TimelineProject } from '../audio/timelineTypes'
import { i18n } from '../i18n'

const t = i18n.global.t

const DEFAULT_PX_PER_SECOND = 40

function newLane(name: string): TimelineLane {
  return { id: crypto.randomUUID(), name, clips: [], settings: defaultChannelSettings() }
}

function emptyProject(): TimelineProject {
  return {
    version: 1,
    lanes: [newLane(t('storeErrors.defaultLane', { n: 1 })), newLane(t('storeErrors.defaultLane', { n: 2 }))],
    master: defaultMasterSettings(),
    pxPerSecond: DEFAULT_PX_PER_SECOND,
  }
}

export const useEditorStore = defineStore('editor', {
  state: () => ({
    projectId: null as number | null,
    projectName: t('storeErrors.newProject'),
    project: emptyProject() as TimelineProject,
    playheadSec: 0,
    playing: false,
    selectedClipId: null as string | null,
    dirty: false,
    loading: false,
    saving: false,
    error: null as string | null,
    history: [] as string[],
    historyIndex: -1,
  }),
  getters: {
    totalDuration: (state) => projectDuration(state.project),
    canUndo: (state) => state.historyIndex > 0,
    canRedo: (state) => state.historyIndex >= 0 && state.historyIndex < state.history.length - 1,
  },
  actions: {
    snapshot() {
      const snap = JSON.stringify(this.project)
      if (this.historyIndex >= 0 && this.historyIndex < this.history.length - 1) {
        this.history.splice(this.historyIndex + 1)
      }
      this.history.push(snap)
      if (this.history.length > 30) {
        this.history.shift()
      }
      this.historyIndex = this.history.length - 1
      this.dirty = true
    },
    undo() {
      if (!this.canUndo) return
      this.historyIndex--
      this.project = JSON.parse(this.history[this.historyIndex])
      this.dirty = true
    },
    redo() {
      if (!this.canRedo) return
      this.historyIndex++
      this.project = JSON.parse(this.history[this.historyIndex])
      this.dirty = true
    },
    commitSnapshot() {
      this.snapshot()
    },
    newProject() {
      this.projectId = null
      this.projectName = t('storeErrors.newProject')
      this.project = emptyProject()
      this.playheadSec = 0
      this.playing = false
      this.selectedClipId = null
      this.history = [JSON.stringify(this.project)]
      this.historyIndex = 0
      this.dirty = false
      this.error = null
    },
    async loadProject(id: number) {
      this.loading = true
      this.error = null
      try {
        const full = await projectsApi.getProject(id)
        this.projectId = full.id
        this.projectName = full.name
        this.project = full.data
        this.playheadSec = 0
        this.playing = false
        this.selectedClipId = null
        this.history = [JSON.stringify(this.project)]
        this.historyIndex = 0
        this.dirty = false
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
      } finally {
        this.loading = false
      }
    },
    async save() {
      this.saving = true
      try {
        if (this.projectId == null) {
          const created = await projectsApi.createProject(this.projectName, this.project)
          this.projectId = created.id
        } else {
          await projectsApi.updateProject(this.projectId, { name: this.projectName, data: this.project })
        }
        this.dirty = false
      } finally {
        this.saving = false
      }
    },
    addLane() {
      const lane = newLane(t('storeErrors.defaultLane', { n: this.project.lanes.length + 1 }))
      this.project.lanes.push(lane)
      this.snapshot()
      return lane
    },
    renameLane(laneId: string, name: string) {
      const lane = this.project.lanes.find((l) => l.id === laneId)
      if (lane) {
        lane.name = name
        this.snapshot()
      }
    },
    removeLane(laneId: string) {
      this.project.lanes = this.project.lanes.filter((l) => l.id !== laneId)
      this.snapshot()
    },
    addClip(laneId: string, clip: Clip) {
      const lane = this.project.lanes.find((l) => l.id === laneId)
      if (lane) {
        lane.clips.push(clip)
        this.snapshot()
      }
    },
    removeClip(clipId: string) {
      for (const lane of this.project.lanes) {
        const idx = lane.clips.findIndex((c) => c.id === clipId)
        if (idx !== -1) {
          lane.clips.splice(idx, 1)
          if (this.selectedClipId === clipId) this.selectedClipId = null
          this.snapshot()
          return
        }
      }
    },
    /**
     * Cuts a clip in two at a point on the timeline.
     *
     * Both halves keep pointing at the same source audio and simply take
     * different trim windows - no audio is copied or re-encoded, which is
     * why this is instant and why splitting a clip fifty times costs
     * nothing. Splitting is the single most-used edit in an arrangement and
     * it has to feel free.
     */
    splitClip(clipId: string, atSec: number) {
      for (const lane of this.project.lanes) {
        const index = lane.clips.findIndex((c) => c.id === clipId)
        if (index === -1) continue
        const clip = lane.clips[index]
        const offset = atSec - clip.timelineStart
        const length = clip.trimEnd - clip.trimStart
        // A cut at or outside an edge would leave a zero-length clip, which
        // cannot be seen, grabbed or deleted afterwards.
        if (offset <= 0.001 || offset >= length - 0.001) return false

        const right: Clip = {
          id: crypto.randomUUID(),
          sourceUrl: clip.sourceUrl,
          sourceLabel: clip.sourceLabel,
          timelineStart: atSec,
          trimStart: clip.trimStart + offset,
          trimEnd: clip.trimEnd,
        }
        clip.trimEnd = clip.trimStart + offset
        lane.clips.splice(index + 1, 0, right)
        this.snapshot()
        return true
      }
      return false
    },

    /** A copy directly after the original, which is what "duplicate" means
     *  in an arrangement - not a copy on top of it. */
    duplicateClip(clipId: string) {
      for (const lane of this.project.lanes) {
        const clip = lane.clips.find((c) => c.id === clipId)
        if (!clip) continue
        const length = clip.trimEnd - clip.trimStart
        const copy: Clip = {
          id: crypto.randomUUID(),
          sourceUrl: clip.sourceUrl,
          sourceLabel: clip.sourceLabel,
          timelineStart: clip.timelineStart + length,
          trimStart: clip.trimStart,
          trimEnd: clip.trimEnd,
        }
        lane.clips.push(copy)
        this.selectedClipId = copy.id
        this.snapshot()
        return copy.id
      }
      return null
    },

    /** Which lane a clip is on - the split and paste paths both need it and
     *  neither should be walking the project itself. */
    findClip(clipId: string): { lane: TimelineLane; clip: Clip } | null {
      for (const lane of this.project.lanes) {
        const clip = lane.clips.find((c) => c.id === clipId)
        if (clip) return { lane, clip }
      }
      return null
    },

    pasteClip(source: Clip, laneId: string, atSec: number) {
      const lane = this.project.lanes.find((l) => l.id === laneId)
      if (!lane) return null
      const copy: Clip = {
        id: crypto.randomUUID(),
        sourceUrl: source.sourceUrl,
        sourceLabel: source.sourceLabel,
        timelineStart: Math.max(0, atSec),
        trimStart: source.trimStart,
        trimEnd: source.trimEnd,
      }
      lane.clips.push(copy)
      this.selectedClipId = copy.id
      this.snapshot()
      return copy.id
    },

    updateClip(clipId: string, patch: Partial<Clip>, commit = false) {
      for (const lane of this.project.lanes) {
        const clip = lane.clips.find((c) => c.id === clipId)
        if (clip) {
          Object.assign(clip, patch)
          if (commit) {
            this.snapshot()
          } else {
            this.dirty = true
          }
          return
        }
      }
    },
    updateLaneSettings(laneId: string, settings: ChannelSettings) {
      const lane = this.project.lanes.find((l) => l.id === laneId)
      if (lane) {
        lane.settings = settings
        this.snapshot()
      }
    },
    updateMasterSettings(settings: MasterSettings) {
      this.project.master = settings
      this.snapshot()
    },
    setZoom(pxPerSecond: number) {
      this.project.pxPerSecond = Math.max(5, Math.min(400, pxPerSecond))
    },
  },
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useEditorStore, import.meta.hot))
}
