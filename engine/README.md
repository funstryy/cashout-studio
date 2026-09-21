# Cashout Studio audio engine

A native real-time audio engine in C++, built with CMake into a Visual
Studio solution. This is the beginning of moving the studio off the browser
audio path, not a rewrite of the whole application.

## What it is

A separate process that owns the audio device and the mixer. The UI drives
it over a loopback socket with newline-delimited JSON, one object per
command, one reply per command.

Separate process on purpose: the studio's front end is a web view, and a web
view stops the world to do layout. An audio thread in that process glitches
whenever somebody opens a panel. Out here the render loop runs on an MMCSS
**Pro Audio** thread with nothing in the address space that can block it.

## What it does today

| | |
|---|---|
| Output | WASAPI, shared and exclusive, event-driven |
| Thread | MMCSS Pro Audio, `AVRT_PRIORITY_CRITICAL` |
| Mixer | 128 tracks, 8192 clips, constant-power pan, smoothed gain, solo/mute, per-track peak metering |
| Plugins | VST3 **live in the signal path**, up to 8 inserts per track plus the master |
| Transport | Sample-accurate position, looping |
| Files | WAV in (8/16/24/32-bit PCM and float), 32-bit float out, resampled to the device rate on load |
| Capture | Always-on 30-second ring on the master bus |
| Mastering | BS.1770 loudness, true peak, spectral matching, true-peak limiting |

### Measured on this machine

AMD system, Windows 10, onboard and virtual devices:

```
shared mode,    1056 frames   22.00 ms   0 xruns   1% peak load
exclusive mode,  480 frames   10.00 ms   0 xruns   2% peak load
exclusive mode,  256 frames    5.33 ms   0 xruns   5% peak load
exclusive mode,  128 frames    2.67 ms   0 xruns   7% peak load
```

With plugins live in the path at 256 frames / 5.33 ms, zero dropouts:

```
dry                        peak 0.0243    1.8% load
OTT                        peak 0.0949    4.1% load     loaded in 202 ms
Bertom Vocal Compressor    peak 0.0292    4.1% load     loaded in  61 ms
Boogex (amp sim)           peak 0.0500   25.2% load     loaded in 141 ms
```

For comparison, the browser path this replaces measures around 22 ms at best
and drops buffers under UI load.

## Building

```powershell
cd engine
.\build.ps1            # full build, VST3 hosting on
.\build.ps1 -NoVst3    # core only, about 20 seconds
.\build.ps1 -Open      # build, then open the solution in Visual Studio
```

Needs CMake and Visual Studio 2022 (Build Tools are enough). VST3 hosting
needs the Steinberg SDK at `external/vst3sdk`, which this repo already has.

The solution lands at `engine/build/cashout_engine.sln`. Open it and you can
set a breakpoint inside the render callback and step through a block, which
is the actual reason for this being C++.

## Using it from the studio

The DAW has an **Audio engine** panel: start it, pick a device and buffer
size, and the latency, CPU load and dropout count are live on screen. The
backend finds the executable automatically at `engine/build/Release/`.

## Playing the timeline

The DAW plays through the engine whenever it has an output open, and falls
back to Web Audio when it does not. There is no setting: the device is the
switch, so "the Audio engine panel says open" is the whole rule.

Pressing play sends the arrangement across (`/api/engine/sync`), which
resolves every clip's URL to a file on disk, loads each one once, and lays
the clips out on the engine's tracks with their gain, pan, mute and solo.
The playhead then follows the engine's own frame count rather than a clock
on this side - the engine is what is making the sound, so it is what knows
where the song is.

Verified against known tones rather than by ear. Two lanes, a 440Hz sine
hard left from 0s and an 880Hz sine hard right from 2s at half gain,
captured off the master and analysed:

```
      window     rmsL     rmsR    440@L    880@R
    0.5-1.5s   0.3535   0.0000   0.5000   0.0000
    3.0-4.0s   0.3511   0.1768   0.4932   0.2500
    5.0-6.0s   0.0000   0.1756   0.0000   0.2467
```

0.3535 is 0.5/sqrt(2) exactly; 0.1768 is 0.25/sqrt(2) exactly. Clip start
and trim land on the right sample, constant-power pan puts nothing at all in
the opposite channel, and lane gain multiplies exactly as asked.

## Mastering

`analyze` measures a file; `masterFile` renders a mastered version of it.
Both work on files and need no audio device, so they run on a machine with
no interface attached.

Measurement is ITU-R BS.1770-4 / EBU R128, implemented from the standard
rather than approximated. The check that matters: a 1 kHz sine at
-20 dBFS is *defined* to be -20.0 LUFS, and this reads

```
1kHz @ -20 dBFS  ->  -20.00 LUFS   error +0.00 LU   true peak -20.00 dBTP
1kHz @ -30 dBFS  ->  -30.00 LUFS   delta -10.00 LU
```

A meter that misses that is wrong no matter how good its output sounds.

The chain is the conventional one in the conventional order, and every
decision comes from a measurement:

```
high-pass -> matching EQ -> glue compressor -> width -> true-peak limiter
```

- **Matching EQ** compares the mix's tonal balance against the target's in
  ten log-spaced bands, and corrects the difference up to a bounded amount.
- **Glue** is chosen by crest factor. A mix already at 8 dB crest gets no
  compression at all; one at 16 dB gets 2:1.
- **The limiter is true-peak aware and offline.** Required gain is computed
  for every sample from a 4x-oversampled peak, then a sliding-window
  minimum over the look-ahead guarantees the ceiling cannot be exceeded.
  This is a property of the construction, not a tuning.

### Reference matching

The mode worth using. Point it at a record you want to sit next to and its
tonal balance and loudness become the target. Measured, matching one library
track to another:

```
                40Hz   78   151   295   573  1116  2172  4226  8222   16k    LUFS
reference       22.7  37.6  39.7  45.5  45.1  41.6  36.1  35.9  24.8 -10.2  -17.06
result          21.9  37.4  39.2  45.1  44.8  41.3  35.7  35.4  24.2 -10.2  -17.06
```

Every band within 0.8 dB, loudness exact.

### Honesty about loudness

A limiter alone cannot take every mix to -7 LUFS. Asked for a target it
cannot reach, the engine converges as far as it can, stops when more
pre-gain stops buying loudness, and reports the shortfall in
`plan.loudnessMissLu` so the UI can say so rather than quietly delivering
something 3 LU quieter than requested.

## Retrospective capture

The engine holds the last 30 seconds of the master bus in a ring from the
moment the device opens, because that costs one memcpy per block and nothing
to leave running.

So "that was the take and I wasn't recording" stops being a thing that can
happen. The audio already exists; the button only writes it down. It lands
in the library as an ordinary track, immediately visible to the DAW, the
separator and the dataset builder.

A few DAWs do this for MIDI, which is the easy half - MIDI is a handful of
bytes a second. Doing it for audio needs an engine that owns its own ring
buffer, which is exactly what this is.

## Protocol

Loopback only, port 9310 by default. No authentication, deliberately: it is
an implementation detail of one machine's studio and must never be reachable
from the collaboration listener.

```json
{"cmd":"devices"}
{"cmd":"open","device":"<id>","sampleRate":48000,"bufferFrames":256,"exclusive":false}
{"cmd":"load","id":"kick","path":"C:\\...\\kick.wav"}
{"cmd":"addClip","track":0,"buffer":"kick","start":0.0,"trimStart":0.0,"trimEnd":4.0,"gain":1.0}
{"cmd":"track","index":0,"gain":1.0,"pan":0.0,"mute":false,"solo":false}
{"cmd":"insert","track":0,"slot":0,"path":"C:\\Program Files\\Common Files\\VST3\\OTT.vst3"}
{"cmd":"play"} {"cmd":"stop"} {"cmd":"seek","seconds":12.5}
{"cmd":"status"} {"cmd":"meters","count":16}
{"cmd":"capture","path":"C:\\...\\take.wav","seconds":30}
```

## Real-time rules

The audio callback takes no lock, allocates nothing, frees nothing, and
touches no file. Everything from outside arrives through a lock-free ring
and is applied at a block boundary. Anything added here has to keep that
true - it is the whole reason the numbers above look the way they do.

## Not done yet

- Audio **input**: capture is output-side only, so there is no recording
  from an interface through this path yet.
- ASIO: WASAPI exclusive gets to 2.67 ms, which is enough for most
  interfaces, but ASIO drivers often do better and some interfaces only
  expose their good modes there.
- The metronome is Web Audio only. The click is scheduled into the browser
  graph and would drift against a playhead driven by the engine, so it is
  skipped on the native path rather than played in the wrong place.
- Plugin editor windows are still the separate `host/` process.
- Unloading a plugin mid-session leaks it deliberately; doing it properly
  needs a retire handshake with the audio thread.
