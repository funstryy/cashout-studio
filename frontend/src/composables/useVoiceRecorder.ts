import { onBeforeUnmount, ref } from 'vue'
import { getSharedAudioCtx } from './audioPlayback'
import { encodeWav } from '../audio/wavEncoder'

/**
 * Microphone capture for the DAW.
 *
 * MediaRecorder hands back webm/opus, which the track library won't accept and
 * the timeline can't trim precisely. So the blob is decoded and re-encoded to
 * WAV here: it costs one pass over the audio and gives the rest of the app the
 * same kind of buffer every other clip already is.
 *
 * The input is metered live off a separate analyser tap - the recorder itself
 * exposes no level, and an arm button with no signal indication is how people
 * end up recording silence for three minutes.
 */
export function useVoiceRecorder() {
  const armed = ref(false)
  const recording = ref(false)
  const level = ref(0)
  const error = ref('')

  let stream: MediaStream | null = null
  let recorder: MediaRecorder | null = null
  let chunks: Blob[] = []
  let analyser: AnalyserNode | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let meterRaf: number | null = null

  function meter(): void {
    if (!analyser) return
    const samples = new Float32Array(analyser.fftSize)
    analyser.getFloatTimeDomainData(samples)
    let peak = 0
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample))
    // Decay rather than snap, so a transient stays visible long enough to see.
    level.value = Math.max(peak, level.value * 0.85)
    meterRaf = requestAnimationFrame(meter)
  }

  async function arm(): Promise<void> {
    if (armed.value) return
    error.value = ''
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Off by design: these are speech-call helpers, and they pump gain
          // and gate pauses in ways that wreck a musical take.
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      })
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return
    }
    const ctx = getSharedAudioCtx()
    source = ctx.createMediaStreamSource(stream)
    analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    source.connect(analyser)
    // Deliberately not connected to the destination: monitoring through the
    // speakers while recording is a feedback loop.
    armed.value = true
    meter()
  }

  function disarm(): void {
    if (meterRaf != null) cancelAnimationFrame(meterRaf)
    meterRaf = null
    source?.disconnect()
    analyser?.disconnect()
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    source = null
    analyser = null
    armed.value = false
    recording.value = false
    level.value = 0
  }

  function start(): void {
    if (!stream || recording.value) return
    chunks = []
    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.start()
    recording.value = true
  }

  /** Stops and returns the take as a WAV blob, or null if nothing was captured. */
  async function stop(): Promise<{ blob: Blob; durationSec: number } | null> {
    if (!recorder || !recording.value) return null
    const done = new Promise<void>((resolve) => {
      recorder!.onstop = () => resolve()
    })
    recorder.stop()
    recording.value = false
    await done

    if (!chunks.length) return null
    const captured = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' })
    const decoded = await getSharedAudioCtx().decodeAudioData(await captured.arrayBuffer())
    return { blob: encodeWav(decoded), durationSec: decoded.duration }
  }

  onBeforeUnmount(disarm)

  return { armed, recording, level, error, arm, disarm, start, stop }
}
