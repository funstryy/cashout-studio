/**
 * The client half of a live session.
 *
 * What this has to get right is the echo problem: a patch that arrives from
 * the network is applied to the local project, the project watcher fires,
 * and without care that change is immediately sent straight back out. Two
 * studios doing that to each other is an infinite loop that saturates the
 * link within a second. So every applied patch also updates the record of
 * what has been sent, which makes the resulting diff empty and the echo
 * stops there.
 *
 * Sending is debounced rather than immediate. Dragging a clip fires the
 * watcher on every animation frame; nobody needs 60 updates a second of an
 * arrangement, and the cursor channel already carries the part that has to
 * feel instant.
 */
import { defineStore } from 'pinia'
import { useEditorStore } from './editor'
import {
  collabStatus,
  parseInvite,
  resolveSource,
  socketUrl,
  startHosting,
  stopHosting,
} from '../api/collab'
import type { CollabPeer, CollabState, InviteParts } from '../api/collab'
import type { TimelineLane, TimelineProject } from '../audio/timelineTypes'
import { withChannelDefaults } from '../audio/mixerEngine'

export type Connection = 'offline' | 'connecting' | 'live' | 'error'

export interface ChatLine {
  id: number
  name: string
  colour: string
  text: string
  system: boolean
}

/** Project fields that belong to everyone. Zoom and selection are views of
 *  the arrangement rather than part of it, so they stay local - having a
 *  friend's scroll wheel change your zoom would be maddening. */
const SHARED_SCALARS = ['master', 'bpm', 'loop', 'rack', 'version'] as const

const SEND_DEBOUNCE_MS = 120
const CURSOR_INTERVAL_MS = 100

let chatSeq = 0

export const useCollabStore = defineStore('collab', {
  state: () => ({
    connection: 'offline' as Connection,
    /**
     * Whether *this machine's* backend has a session open.
     *
     * Not the same question as "am I the host of the session I am in", and
     * conflating the two was a bug: someone who hosts, ends it, then joins a
     * friend keeps a backend that still reports a session, and the guest
     * logic then skipped adopting the friend's project and offered to end a
     * session that was not theirs. Role below answers the second question.
     */
    hosting: false,
    role: '' as '' | 'host' | 'guest',
    /** Set on a guest, null on the host: where shared audio actually lives. */
    remote: null as InviteParts | null,
    selfId: '',
    selfName: '',
    peers: [] as CollabPeer[],
    cursors: {} as Record<string, number>,
    chat: [] as ChatLine[],
    unread: 0,
    invite: '',
    addresses: [] as string[],
    port: 9000,
    error: '',
    revision: 0,
  }),
  getters: {
    /** Everyone but you - the list worth putting on screen. */
    others(state): CollabPeer[] {
      return state.peers.filter((peer) => peer.peer_id !== state.selfId)
    },
    live(state): boolean {
      return state.connection === 'live'
    },
    /** True only while hosting the session this studio is actually in. */
    isHost(state): boolean {
      return state.role === 'host'
    },
  },
  actions: {
    async refresh() {
      try {
        this.applyServerState(await collabStatus())
      } catch {
        // The status endpoint failing means the backend is down, which the
        // rest of the app already reports loudly.
      }
    },

    applyServerState(state: CollabState) {
      this.hosting = state.active
      this.invite = state.code
      this.addresses = state.addresses
      this.port = state.port
    },

    /** Opens a session on this machine and joins it as the host. */
    async host(name: string, address?: string) {
      const editor = useEditorStore()
      this.error = ''
      this.connection = 'connecting'
      try {
        const state = await startHosting(editor.project, editor.projectName, address)
        this.applyServerState(state)
        // Loopback, not the VPN address: the host's own traffic has no
        // reason to leave the machine.
        this.connect({ host: '127.0.0.1', port: state.port, token: state.token }, name, true)
      } catch (err) {
        this.connection = 'error'
        this.error = err instanceof Error ? err.message : String(err)
      }
    },

    /**
     * Joins somebody else's session from a pasted invite code.
     *
     * The token is checked over HTTP before the socket is opened. A server
     * that refuses a WebSocket during the handshake cannot tell the browser
     * why - the page only ever sees close code 1006 - so going straight to
     * the socket meant a stale token reported itself as "check that Radmin
     * is connected", which sends somebody debugging their VPN over a code
     * that simply expired. One request first separates the three cases
     * cleanly, and costs nothing next to the session that follows.
     */
    async join(invite: string, name: string) {
      const parts = parseInvite(invite)
      if (!parts) {
        this.error = 'that invite code does not look right'
        this.connection = 'error'
        return
      }
      this.error = ''
      this.connection = 'connecting'

      let state: CollabState
      try {
        const probe = await fetch(
          `http://${parts.host}:${parts.port}/api/collab/status?ct=${encodeURIComponent(parts.token)}`,
          // A wrong address otherwise sits on a TCP connect for the best
          // part of a minute with nothing on screen but a disabled button.
          { signal: AbortSignal.timeout(6000) },
        )
        if (probe.status === 403) {
          this.error = 'that invite code was rejected - ask for a fresh one'
          this.connection = 'error'
          return
        }
        state = (await probe.json()) as CollabState
      } catch {
        this.error = 'could not reach that studio - check the address and that Radmin is connected'
        this.connection = 'error'
        return
      }

      if (!state.active) {
        this.error = 'that studio is not sharing a session right now'
        this.connection = 'error'
        return
      }

      this.remote = parts
      this.connect(parts, name, false)
    },

    async leave() {
      const wasHost = this.role === 'host'
      this.role = ''
      teardown()
      if (this.remote) forgetSessionToken(this.remote)
      if (wasHost) {
        try {
          this.applyServerState(await stopHosting())
        } catch {
          // Already stopped, or the backend went away. Either way this side
          // is offline now.
        }
      }
      this.connection = 'offline'
      this.hosting = false
      this.remote = null
      this.peers = []
      this.cursors = {}
      this.selfId = ''
    },

    connect(parts: InviteParts, name: string, asHost: boolean) {
      teardown()
      this.role = asHost ? 'host' : 'guest'
      this.selfName = name || 'Guest'
      this.connection = 'connecting'
      openSocket(this, socketUrl(parts, this.selfName, asHost), asHost)
    },

    /** Called by the timeline as the playhead moves. Rate-limited here so
     *  callers can fire it as often as they like. */
    sendCursor(playhead: number, selectedClip: string | null) {
      const now = performance.now()
      if (now - lastCursorAt < CURSOR_INTERVAL_MS) return
      lastCursorAt = now
      send({ type: 'cursor', playhead, selected_clip: selectedClip })
    },

    sendTransport(playing: boolean, playhead: number) {
      send({ type: 'transport', playing, playhead })
    },

    say(text: string) {
      const clean = text.trim()
      if (clean) send({ type: 'chat', text: clean })
    },

    pushChat(line: Omit<ChatLine, 'id'>) {
      this.chat.push({ ...line, id: ++chatSeq })
      if (this.chat.length > 200) this.chat.shift()
      this.unread++
    },
  },
})

// --------------------------------------------------------------- the socket

let socket: WebSocket | null = null
let sendTimer: ReturnType<typeof setTimeout> | null = null
let stopWatching: (() => void) | null = null
let lastCursorAt = 0
/** JSON of every lane as it was last agreed with the server. */
let lastSent = new Map<string, string>()
let lastScalars = ''
let applying = false

function send(message: unknown) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
}

function teardown() {
  if (sendTimer) clearTimeout(sendTimer)
  sendTimer = null
  stopWatching?.()
  stopWatching = null
  lastSent = new Map()
  lastScalars = ''
  if (socket) {
    // Cleared first: the close handler must not treat a deliberate leave as
    // a dropped connection.
    const dying = socket
    socket = null
    dying.onclose = null
    dying.close()
  }
}

type Store = ReturnType<typeof useCollabStore>
type Editor = ReturnType<typeof useEditorStore>

function openSocket(store: Store, url: string, asHost: boolean) {
  const ws = new WebSocket(url)
  socket = ws

  ws.onmessage = (event) => {
    let message: Record<string, unknown>
    try {
      message = JSON.parse(event.data)
    } catch {
      return
    }
    handle(store, message)
  }

  ws.onerror = () => {
    store.error = asHost
      ? 'the session could not start'
      : 'could not reach that studio - check the address and that Radmin is connected'
  }

  ws.onclose = (event) => {
    if (socket !== ws) return
    socket = null
    store.connection = 'offline'
    store.peers = []
    store.cursors = {}
    // 1000 is the host ending the session, which already said so in a
    // message of its own. Anything else mid-session is the link dropping,
    // and saying so beats a panel that quietly goes back to the join form.
    // A refused handshake never reaches here with a useful code - join()
    // establishes the reason before the socket is opened.
    if (event.code !== 1000 && !store.error) {
      store.error = 'the connection to that studio dropped'
    }
  }
}

function handle(store: Store, message: Record<string, unknown>) {
  const editor = useEditorStore()

  switch (message.type) {
    case 'welcome': {
      store.selfId = String(message.peer_id ?? '')
      store.peers = (message.peers as CollabPeer[]) ?? []
      store.revision = Number(message.revision ?? 0)
      store.connection = 'live'
      store.error = ''
      // The host already has this project; a guest is seeing it for the
      // first time and must not keep whatever they had open.
      if (store.role === 'guest') {
        adoptProject(editor, message.project as TimelineProject, store.remote)
        editor.projectName = String(message.project_name ?? editor.projectName)
      }
      recordSent(editor.project, store.remote)
      startWatching(store)
      break
    }

    case 'peers':
      store.peers = (message.peers as CollabPeer[]) ?? []
      break

    case 'patch':
      applyPatch(store, editor, message)
      break

    case 'cursor':
    case 'transport':
      store.cursors = { ...store.cursors, [String(message.peer_id)]: Number(message.playhead ?? 0) }
      break

    case 'chat': {
      const peerId = String(message.peer_id ?? '')
      const peer = store.peers.find((p) => p.peer_id === peerId)
      store.pushChat({
        name: String(message.name ?? peer?.name ?? ''),
        colour: String(message.colour ?? peer?.colour ?? '#8aa0b4'),
        text: String(message.text ?? ''),
        system: Boolean(message.system),
      })
      break
    }

    case 'ended':
      store.error = String(message.reason ?? 'the session ended')
      void store.leave()
      break
  }
}

// ------------------------------------------------------------- project sync

function laneKey(lane: TimelineLane): string {
  return JSON.stringify(lane)
}

function scalarKey(project: TimelineProject): string {
  return JSON.stringify(SHARED_SCALARS.map((key) => (project as unknown as Record<string, unknown>)[key]))
}

function recordSent(project: TimelineProject, remote: InviteParts | null) {
  lastSent = new Map(
    project.lanes.map((lane) => [lane.id, laneKey(retargetLane(lane, remote, true))]),
  )
  lastScalars = scalarKey(project)
}

const ABSOLUTE_SOURCE = /^https?:\/\/[^/]+(\/api\/[^?]*)/

/**
 * Takes the dead session token back out of the project on the way out.
 *
 * A guest who leaves and then saves would otherwise write the host's token
 * into their project file, where it is both a stale credential and no use
 * to anybody - it stops working the moment the session ends. The host's
 * address stays, because a clip that says where its audio lived is more
 * honest than one silently repointed at whatever track shares that id in
 * this library.
 */
function forgetSessionToken(remote: InviteParts) {
  const editor = useEditorStore()
  const dead = `ct=${encodeURIComponent(remote.token)}`
  for (const lane of editor.project.lanes) {
    for (const clip of lane.clips) {
      if (!clip.sourceUrl.includes(dead)) continue
      clip.sourceUrl = clip.sourceUrl.replace(/[?&]ct=[^&]*/, '')
    }
  }
}

/**
 * Rewrites a lane's clip sources for whichever side is holding it.
 *
 * Going out, absolute URLs collapse back to the host-relative form the host
 * stored. Coming in, relative URLs are pointed at the host and carry the
 * session token. Without this a guest's timeline shows every clip and plays
 * none of them - or worse, plays whatever happens to be track 7 in their own
 * library.
 */
function retargetLane(lane: TimelineLane, remote: InviteParts | null, outbound: boolean): TimelineLane {
  // Outbound lanes go as they are when there is nothing to rewrite, but an
  // inbound one is always rebuilt: it came off the wire and may be missing
  // fields the mixer reads without checking.
  if (outbound) {
    if (!remote) return lane
    return {
      ...lane,
      clips: lane.clips.map((clip) => {
        const match = ABSOLUTE_SOURCE.exec(clip.sourceUrl)
        return match ? { ...clip, sourceUrl: match[1] } : clip
      }),
    }
  }
  return {
    ...lane,
    clips: lane.clips.map((clip) => ({ ...clip, sourceUrl: resolveSource(clip.sourceUrl, remote) })),
    settings: withChannelDefaults(lane.settings),
  }
}

function adoptProject(editor: Editor, project: TimelineProject, remote: InviteParts | null) {
  applying = true
  editor.project = {
    ...project,
    lanes: project.lanes.map((lane) => retargetLane(lane, remote, false)),
    master: { ...editor.project.master, ...(project.master ?? {}) },
    // Zoom stays wherever this person had it.
    pxPerSecond: editor.project.pxPerSecond,
  }
  editor.selectedClipId = null
  applying = false
}

function applyPatch(store: Store, editor: Editor, message: Record<string, unknown>) {
  applying = true
  try {
    const project = editor.project

    const lanes = message.lanes as TimelineLane[] | undefined
    if (Array.isArray(lanes)) {
      for (const incoming of lanes) {
        const lane = retargetLane(incoming, store.remote, false)
        const index = project.lanes.findIndex((l) => l.id === lane.id)
        if (index >= 0) project.lanes[index] = lane
        else project.lanes.push(lane)
        // Recorded as already-agreed, so the watcher this triggers produces
        // an empty diff instead of sending the change straight back.
        lastSent.set(lane.id, laneKey(incoming))
      }
    }

    const removed = message.removed_lanes as string[] | undefined
    if (Array.isArray(removed)) {
      for (const id of removed) {
        project.lanes = project.lanes.filter((lane) => lane.id !== id)
        lastSent.delete(id)
      }
    }

    const order = message.lane_order as string[] | undefined
    if (Array.isArray(order) && order.length) {
      const byId = new Map(project.lanes.map((lane) => [lane.id, lane]))
      const reordered = order.map((id) => byId.get(id)).filter(Boolean) as TimelineLane[]
      // Anything the sender did not mention stays, at the end - dropping it
      // would delete a lane the other side simply had not heard about yet.
      for (const lane of project.lanes) if (!order.includes(lane.id)) reordered.push(lane)
      project.lanes = reordered
    }

    const scalars = message.scalars as Record<string, unknown> | undefined
    if (scalars) Object.assign(project, scalars)

    lastScalars = scalarKey(project)
    store.revision = Number(message.revision ?? store.revision)
    editor.dirty = true
  } finally {
    applying = false
  }
}

function startWatching(store: Store) {
  const editor = useEditorStore()
  stopWatching?.()
  stopWatching = editor.$subscribe(() => {
    if (applying || !socket) return
    if (sendTimer) clearTimeout(sendTimer)
    sendTimer = setTimeout(() => flush(store, editor), SEND_DEBOUNCE_MS)
  })
}

function flush(store: Store, editor: Editor) {
  sendTimer = null
  if (!socket || socket.readyState !== WebSocket.OPEN) return

  const project = editor.project
  const patch: Record<string, unknown> = {}

  const changed: TimelineLane[] = []
  const present = new Set<string>()
  for (const lane of project.lanes) {
    present.add(lane.id)
    const outbound = retargetLane(lane, store.remote, true)
    const key = laneKey(outbound)
    if (lastSent.get(lane.id) !== key) {
      changed.push(outbound)
      lastSent.set(lane.id, key)
    }
  }
  if (changed.length) patch.lanes = changed

  const gone = [...lastSent.keys()].filter((id) => !present.has(id))
  if (gone.length) {
    patch.removed_lanes = gone
    for (const id of gone) lastSent.delete(id)
  }

  if (changed.length || gone.length) patch.lane_order = project.lanes.map((lane) => lane.id)

  const scalars = scalarKey(project)
  if (scalars !== lastScalars) {
    lastScalars = scalars
    patch.scalars = Object.fromEntries(
      SHARED_SCALARS.map((key) => [key, (project as unknown as Record<string, unknown>)[key]]),
    )
  }

  if (Object.keys(patch).length) send({ type: 'patch', ...patch })
}
