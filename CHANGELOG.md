# Changelog

Notable changes, newest first. Dates are the day the build was cut.

## 1.0.0

The first release anyone else can install.

### Added

**A native audio engine.** A separate C++ process talking to WASAPI on an
MMCSS Pro Audio thread, with lock-free queues between it and the
interface. Measured at 2.67 ms output latency with zero dropouts while the
interface was being driven hard. Shared and exclusive device modes.

**VST3 hosting.** Plugins run live in the signal path when the engine owns
the device. On the browser playback path, where native code cannot run in
the page, a plugin is printed onto the track instead and listed so it can
be reopened with the settings that print used.

**AI mastering.** Integrated loudness to ITU-R BS.1770-4 with K-weighting
and EBU R128 gating, loudness range, and true peak through 4x oversampling
with a 32-tap windowed sinc. Designs an EQ and limiter chain from the gap
between the mix and a target, or against a reference track you supply.

**An AI co-producer.** Listens to the master bus and reports what it
measured: key from a chroma profile matched against the
Krumhansl-Schmuckler profiles, chords from frame-by-frame triad template
matching, printed as roman numerals. Recognises the four rotations of
I-V-vi-IV and says so. Can write an alternative progression and loosen
mechanically quantised timing.

**Live collaboration.** One host, any number of guests, over a LAN or a
VPN. The host opens a second listener whose reachable surface is limited
by explicit path patterns to the song, its mixdown and its stems. The
audio engine is never exposed on it.

**Stem separation.** BS-RoFormer, Mel-Band RoFormer and HTDemucs, singly
or as an ensemble, with a sample mode for cards that cannot hold a whole
song.

**Help, About and first-run tutorials.** 38 answers searched across both
questions and answers, a credits page, and a different short tutorial on
each of the first four launches.

**An installer.** One signed-by-nobody but self-contained Windows
executable. No Python, git, compiler or package manager on the target
machine. Model weights are downloaded during setup with resume, and the
disk-space check refuses a download that will not fit before it starts.

### Changed

**The DAW is a workstation rather than a page.** It was a scrolling column
of stacked panels, so the transport scrolled away mid-take. The shell is
now exactly the height of the window: pinned chrome, the arrangement
taking what is left, a resizable dock for the rack and mixer, and the
tools in a rail beside the song.

**Track headers went from 220px to 72px**, carrying only what you touch
while the transport rolls. The full channel strip, plugins and delete
moved one click away.

**The visual language.** 2px radii, faces lit from above, keys that travel
when pressed, desk faders with a moulded cap in a cut slot, segmented
meters, engraved lettering and status lamps.

**A menu bar**, with shortcuts printed beside every entry.

### Fixed

- The intro animation never played. `attemptPlay()` read the video ref
  before Vue had rendered the `v-if` branch, so it was always null and the
  splash dismissed itself in about 300 ms, silently.
- An engine left running after the studio was killed held its port and the
  audio device. Engines are now in a Windows job object and die with the
  parent, and the port falls back when one is taken.
- "The engine exited immediately" now quotes what the engine printed,
  which is usually the whole explanation.
- The engine readiness check could block forever on a blocking read inside
  a deadline loop it could never reach. It is now a ping handshake, which
  also proves the thing that answered is the engine.
- The mastering panel showed two empty dropdowns whenever the engine was
  down, because one `Promise.all` discarded the track list that had loaded
  fine.
- Guests could not hear host stems: the path match missed `/stems/`, and
  there were no CORS headers, including on the 403.
- A missing `eq` object crashed the mixer render.
- The whole app could freeze with a blank body. A page transition using
  `mode="out-in"` waits on requestAnimationFrame, which a WebView stops
  firing when occluded, so the leave never completed.
- Mastering overshot the ceiling by 3 dB. The offline limiter now uses a
  sliding-window minimum and lands on the target exactly.
- Spectrum analysis summed to mono before the FFT, so out-of-phase bass
  vanished and bass-heavy mixes were reported as thin.

### Known limitations

- Windows only.
- The installer is unsigned, so SmartScreen warns on first run. Documented
  in `INSTALL.md`, with a checksum to verify against.
- YuE2 weights are CC BY-NC 4.0. Tracks generated through YuE2 cannot be
  used commercially without separate permission.
- ACE-Step runs on CPU on AMD hardware and is slow there.
- If port 9000 is taken the app falls back to another one, and the browser
  engine treats that as a different site, so the profile and preferences
  appear to reset.
