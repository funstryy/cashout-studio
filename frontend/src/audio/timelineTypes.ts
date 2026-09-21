import type { ChannelSettings, MasterSettings } from './mixerEngine'

export interface Clip {
  id: string
  sourceUrl: string
  sourceLabel: string
  /** Position on the global timeline, in seconds. */
  timelineStart: number
  /** Offset into the source audio where playback of this clip begins, in seconds. */
  trimStart: number
  /** Offset into the source audio where playback of this clip ends, in seconds. */
  trimEnd: number
}

/**
 * A plugin that has been printed onto a track.
 *
 * This is a record of what was done, not a live effect chain: a native plugin
 * cannot sit in the browser's signal path, so applying one renders the clips
 * through it and replaces them. Keeping the list means a track can still say
 * what it has been through, and a plugin can be reopened with the settings
 * that print used.
 */
export interface AppliedPlugin {
  /** As the plugin reports itself - this is what shows on the track. */
  name: string
  /** The bundle, so its window can be reopened from the track. */
  path: string
  /** The saved settings this print used. */
  stateKey: string
  /** When it was printed, so the order shown is the order it happened. */
  appliedAt: string
}

export interface TimelineLane {
  id: string
  name: string
  clips: Clip[]
  settings: ChannelSettings
  /** Optional so projects saved before this existed still load. */
  plugins?: AppliedPlugin[]
}

/**
 * One note in a channel's piano roll.
 *
 * Positions are in steps rather than seconds, deliberately: a pattern has to
 * survive a tempo change, and a note recorded at 140 BPM that stays on beat
 * three when the project moves to 128 is the whole point of a sequencer.
 */
export interface RackNote {
  id: string
  /** Where it starts, in steps from the top of the pattern. Fractional is
   *  allowed - that is how a note lands off the grid after a nudge. */
  start: number
  /** How long it sounds, in steps. */
  length: number
  /** MIDI note number. 60 is middle C, which is also the channel's own
   *  unpitched playback rate - a note at 60 sounds like the raw sample. */
  key: number
  /** 0..1. Scales the note's gain, not the channel's. */
  velocity: number
}

/** One sample, the steps that fire it, and the notes that pitch it. */
export interface RackChannel {
  id: string
  name: string
  sourceUrl: string
  /** One flag per step; the array's length is the pattern length. */
  steps: boolean[]
  settings: ChannelSettings
  /** Semitones, so one sample can cover more than one note. */
  pitch?: number
  /**
   * The piano roll's contents.
   *
   * A channel is either a step channel or a note channel, the same way FL
   * Studio works: once there are notes, the step row stops being what plays
   * and becomes a summary of it. Keeping both on one channel rather than
   * splitting the types means a hi-hat programmed on the grid can be opened
   * in the piano roll and given real pitches without being rebuilt.
   */
  notes?: RackNote[]
  /** Whether the notes or the steps are what sounds. Set the moment the
   *  first note is drawn, so the two can never both play at once. */
  mode?: 'steps' | 'notes'
}

export const MIDDLE_C = 60

export function channelNotes(channel: RackChannel): RackNote[] {
  return channel.notes ?? []
}

export function channelMode(channel: RackChannel): 'steps' | 'notes' {
  return channel.mode ?? (channel.notes?.length ? 'notes' : 'steps')
}

export interface ChannelRack {
  channels: RackChannel[]
  /** A bar is four beats, so 16 is a sixteenth-note grid. */
  stepsPerBar: number
  bars: number
}

export interface LoopRegion {
  enabled: boolean
  startSec: number
  endSec: number
}

export interface TimelineProject {
  version: 1
  lanes: TimelineLane[]
  master: MasterSettings
  pxPerSecond: number
  /** Optional so projects saved before the transport existed still load. */
  bpm?: number
  loop?: LoopRegion
  /** Optional so projects saved before the rack existed still load. */
  rack?: ChannelRack
}

export const DEFAULT_BPM = 100

export function projectBpm(project: TimelineProject): number {
  return project.bpm ?? DEFAULT_BPM
}

export function projectLoop(project: TimelineProject): LoopRegion {
  return project.loop ?? { enabled: false, startSec: 0, endSec: 0 }
}

export function lanePlugins(lane: TimelineLane): AppliedPlugin[] {
  return lane.plugins ?? []
}

export function clipDuration(clip: Clip): number {
  return clip.trimEnd - clip.trimStart
}

export function clipEnd(clip: Clip): number {
  return clip.timelineStart + clipDuration(clip)
}

export function projectDuration(project: TimelineProject): number {
  let max = 0
  for (const lane of project.lanes) {
    for (const clip of lane.clips) {
      max = Math.max(max, clipEnd(clip))
    }
  }
  return max
}
