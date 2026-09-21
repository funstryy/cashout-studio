"""The co-producer.

What this is, stated plainly: a producer's checklist driven by real
measurements of your actual audio, not a language model guessing at what a
mix might sound like. Every note below comes from a number the engine
measured a second ago, and every note that offers to change something knows
exactly which lane and by how much.

That is a deliberate choice rather than a limitation. A model that has
never heard the audio can only produce plausible-sounding advice, and
plausible-sounding advice about a mix is worse than none - you cannot tell
whether it heard the problem or invented it. A note here can always be
checked against the number that produced it, which is why every one of them
carries that number.

The rules come from the things that actually go wrong, in rough order of
how often they ruin a track:

    clipping > mono collapse > masking > balance > tonal > dynamics
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Band centres the engine reports, in Hz. Kept in step with
# dsp::SpectralProfile - ten log-spaced bands from 40Hz to 16kHz.
BAND_NAMES = [
    "sub", "bass", "low", "low-mid", "mid",
    "upper-mid", "presence", "brilliance", "air", "top",
]


@dataclass
class Action:
    """Something the co-producer can do, if asked."""
    kind: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Note:
    id: str
    severity: str        # critical | warn | info | good
    title: str
    detail: str
    # The measurement behind it, so the note can be checked rather than
    # believed.
    evidence: str = ""
    action: Optional[Action] = None

    def as_dict(self) -> dict:
        out = {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
        }
        if self.action:
            out["action"] = {
                "kind": self.action.kind,
                "label": self.action.label,
                "params": self.action.params,
            }
        return out


def _band(analysis: dict, index: int) -> float:
    bands = analysis.get("bands") or []
    return float(bands[index]["db"]) if index < len(bands) else -120.0


def listen_notes(analysis: dict, track_peaks: list[dict], project: dict | None) -> list[Note]:
    """Notes about what is playing right now."""
    notes: list[Note] = []

    lufs = float(analysis.get("lufs", -70.0))
    true_peak = float(analysis.get("truePeakDb", -120.0))
    crest = float(analysis.get("crestDb", 0.0))
    correlation = float(analysis.get("correlation", 1.0))
    lra = float(analysis.get("lra", 0.0))

    # ---- the ones that make a track unusable -----------------------------

    if true_peak > 0.0:
        notes.append(Note(
            id="clipping",
            severity="critical",
            title="You're clipping",
            detail="The master is over full scale. This will distort on export and "
                   "every lossy encoder will make it worse.",
            evidence=f"true peak {true_peak:+.2f} dBTP",
            action=Action("master", "Master it to -1 dBTP", {"target": "streaming"}),
        ))
    elif true_peak > -0.5:
        notes.append(Note(
            id="no_headroom",
            severity="warn",
            title="No headroom left",
            detail="Under half a dB to spare. Encoders overshoot, so this will clip "
                   "on Spotify even though it does not clip here.",
            evidence=f"true peak {true_peak:+.2f} dBTP",
            action=Action("master", "Master it to -1 dBTP", {"target": "streaming"}),
        ))

    # Correlation below zero means the two channels are fighting: on a phone
    # speaker, which sums to mono, the out-of-phase content disappears.
    if correlation < -0.1:
        notes.append(Note(
            id="phase",
            severity="critical",
            title="This will vanish in mono",
            detail="The channels are out of phase with each other. Anyone listening "
                   "on a phone speaker or a club PA will lose most of what you hear.",
            evidence=f"correlation {correlation:+.2f} (below zero is out of phase)",
            action=Action("narrow", "Pull the width in", {"scale": 0.6}),
        ))
    elif correlation < 0.2:
        notes.append(Note(
            id="phase_risky",
            severity="warn",
            title="Mono compatibility is shaky",
            detail="Wide enough that a mono system will thin it out noticeably.",
            evidence=f"correlation {correlation:+.2f}",
        ))

    # ---- tonal balance ---------------------------------------------------

    sub, bass, low = _band(analysis, 0), _band(analysis, 1), _band(analysis, 2)
    mid, upper = _band(analysis, 4), _band(analysis, 5)
    presence, air = _band(analysis, 7), _band(analysis, 8)

    if bass - mid > 9.0:
        notes.append(Note(
            id="bass_heavy",
            severity="warn",
            title="The low end is running the track",
            detail="Bass is a long way above the midrange. It will sound big on "
                   "headphones and disappear on anything small.",
            evidence=f"bass {bass:.0f} dB against mid {mid:.0f} dB, a {bass - mid:.0f} dB gap",
            action=Action("master", "Balance it", {"target": "streaming"}),
        ))
    elif mid - bass > 10.0:
        notes.append(Note(
            id="thin",
            severity="warn",
            title="It's thin",
            detail="Almost nothing under 150Hz. Next to a finished record this will "
                   "sound like it is playing through a laptop.",
            evidence=f"bass {bass:.0f} dB against mid {mid:.0f} dB",
        ))

    if presence - mid > 6.0:
        notes.append(Note(
            id="harsh",
            severity="warn",
            title="Harsh through the presence range",
            detail="3-5kHz is pushed hard. This is the range that gets fatiguing "
                   "after a couple of minutes.",
            evidence=f"presence {presence:.0f} dB against mid {mid:.0f} dB",
        ))

    if mid - air > 22.0:
        notes.append(Note(
            id="dull",
            severity="info",
            title="No air up top",
            detail="Very little above 8kHz. Worth checking whether something in the "
                   "chain is rolling it off.",
            evidence=f"air {air:.0f} dB against mid {mid:.0f} dB",
        ))

    if sub > bass + 4.0:
        notes.append(Note(
            id="subsonic",
            severity="info",
            title="Energy below the speakers",
            detail="More at 40Hz than at 80Hz. Nothing plays that back; it only "
                   "eats headroom your limiter wants.",
            evidence=f"sub {sub:.0f} dB against bass {bass:.0f} dB",
        ))

    # ---- dynamics --------------------------------------------------------

    if crest < 6.0 and lufs > -20.0:
        notes.append(Note(
            id="squashed",
            severity="warn",
            title="It's already squashed flat",
            detail="Barely any peak-to-average left. Mastering cannot make this "
                   "louder, only smaller - the loudness has to come from the mix.",
            evidence=f"crest {crest:.1f} dB, range {lra:.1f} LU",
        ))
    elif crest > 18.0:
        notes.append(Note(
            id="dynamic",
            severity="info",
            title="Lots of dynamic range left",
            detail="Plenty of room for glue compression if you want it denser.",
            evidence=f"crest {crest:.1f} dB",
        ))

    if -70.0 < lufs < -24.0:
        notes.append(Note(
            id="quiet",
            severity="info",
            title="Quiet",
            detail="Nothing wrong with mixing quiet - just know this is about 10 dB "
                   "below where it will end up.",
            evidence=f"{lufs:.1f} LUFS",
        ))

    notes.extend(_balance_notes(track_peaks, project))

    if not notes:
        notes.append(Note(
            id="clean",
            severity="good",
            title="Nothing to flag",
            detail="Levels, phase and tonal balance all look reasonable from here.",
            evidence=f"{lufs:.1f} LUFS, {true_peak:+.1f} dBTP, correlation {correlation:+.2f}",
        ))
    return notes


def _balance_notes(track_peaks: list[dict], project: dict | None) -> list[Note]:
    """Who is too loud, and who cannot be heard."""
    notes: list[Note] = []
    if not project:
        return notes

    lanes = project.get("lanes") or []
    # Only lanes that are actually making a sound right now. A lane that is
    # silent because nothing is playing on it yet is not a balance problem.
    live: list[tuple[int, str, float]] = []
    for index, lane in enumerate(lanes):
        if index >= len(track_peaks):
            break
        peak = max(float(track_peaks[index].get("l", 0.0)),
                   float(track_peaks[index].get("r", 0.0)))
        if peak > 0.002:
            live.append((index, lane.get("name") or f"Track {index + 1}", peak))

    if len(live) < 2:
        return notes

    live.sort(key=lambda item: item[2], reverse=True)
    loudest = live[0]
    quietest = live[-1]

    import math

    def db(value: float) -> float:
        return 20.0 * math.log10(max(value, 1e-9))

    spread = db(loudest[2]) - db(quietest[2])
    if spread > 24.0:
        notes.append(Note(
            id=f"buried_{quietest[0]}",
            severity="warn",
            title=f"“{quietest[1]}” is buried",
            detail=f"It is {spread:.0f} dB under “{loudest[1]}”. Either it is "
                   "meant to be that far back, or you have stopped hearing it.",
            evidence=f"{db(quietest[2]):.0f} dB against {db(loudest[2]):.0f} dB",
            action=Action(
                "set_lane_gain",
                f"Bring “{quietest[1]}” up 6 dB",
                {"lane": quietest[0], "deltaDb": 6.0},
            ),
        ))

    # Everything panned dead centre is the single most common reason a mix
    # sounds small, and the easiest thing to fix.
    centred = [
        (index, lane.get("name") or f"Track {index + 1}")
        for index, lane in enumerate(lanes)
        if abs(float((lane.get("settings") or {}).get("pan", 0.0))) < 0.05
        and any(item[0] == index for item in live)
    ]
    if len(centred) >= 4:
        notes.append(Note(
            id="all_centred",
            severity="info",
            title="Everything is in the middle",
            detail=f"{len(centred)} lanes are all panned dead centre, so they are "
                   "competing for the same space. Moving two of them apart will open "
                   "it up more than any EQ will.",
            evidence=f"{len(centred)} lanes at pan 0",
            action=Action(
                "spread_pan",
                "Spread them out",
                {"lanes": [index for index, _ in centred]},
            ),
        ))
    return notes


def arrangement_notes(project: dict) -> list[Note]:
    """Notes about the shape of the song, which need no audio at all."""
    notes: list[Note] = []
    lanes = project.get("lanes") or []
    clips = [clip for lane in lanes for clip in (lane.get("clips") or [])]

    if not clips:
        return [Note(
            id="empty",
            severity="info",
            title="Nothing on the timeline yet",
            detail="Drop something in and I'll start listening.",
        )]

    def clip_end(clip: dict) -> float:
        start = float(clip.get("timelineStart") or 0.0)
        return start + float(clip.get("trimEnd") or 0.0) - float(clip.get("trimStart") or 0.0)

    length = max((clip_end(clip) for clip in clips), default=0.0)

    if length < 30.0:
        notes.append(Note(
            id="short",
            severity="info",
            title="It's a loop, not a song yet",
            detail=f"{length:.0f} seconds end to end. Whatever you do next, the "
                   "arrangement is the thing standing between this and a track.",
            evidence=f"{length:.0f}s, {len(clips)} clips",
        ))

    # Everything starting at zero means one block repeated, which is the
    # single clearest sign a loop has not become an arrangement.
    at_zero = sum(1 for clip in clips if float(clip.get("timelineStart") or 0.0) < 0.05)
    if len(clips) >= 3 and at_zero == len(clips):
        notes.append(Note(
            id="no_arrangement",
            severity="info",
            title="Every part starts at the same moment",
            detail="Nothing enters or drops out. Even muting one lane for eight bars "
                   "would give it somewhere to go.",
            evidence=f"all {len(clips)} clips start at 0:00",
        ))

    silent = [
        (index, lane.get("name") or f"Track {index + 1}")
        for index, lane in enumerate(lanes)
        if not (lane.get("clips") or [])
    ]
    if len(silent) >= 3:
        notes.append(Note(
            id="empty_lanes",
            severity="info",
            title=f"{len(silent)} empty lanes",
            detail="Not a problem, just clutter - they make the mixer harder to read.",
            evidence=", ".join(name for _, name in silent[:4]),
        ))
    return notes
