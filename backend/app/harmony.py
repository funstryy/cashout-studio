"""Musical judgement, on top of the chords the engine heard.

The engine turns audio into a key and a chord sequence. This turns that
sequence into an opinion - which is a different kind of problem, and one
where being specific matters more than being clever. "Your progression is
boring" helps nobody; "that is the axis progression, four bars of I-V-vi-IV,
the same four chords as most of the last twenty years of pop - try
vi-IV-I-V for the same chords in an order that does not announce itself"
is something you can act on.

Nothing here is a matter of taste dressed up as fact. Every note names what
was played, why it reads the way it does, and what specifically to try.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Semitones above the tonic for each scale degree.
MAJOR_DEGREES = {0: "I", 2: "ii", 4: "iii", 5: "IV", 7: "V", 9: "vi", 11: "vii"}
MINOR_DEGREES = {0: "i", 2: "ii", 3: "III", 5: "iv", 7: "v", 8: "VI", 10: "VII"}

# The four chords, in every rotation anybody actually writes them in. It is
# not a bad progression - it is the most effective one there is, which is
# exactly why hearing it unaltered is worth mentioning.
AXIS_SETS = [
    (["I", "V", "vi", "IV"], "I-V-vi-IV"),
    (["vi", "IV", "I", "V"], "vi-IV-I-V"),
    (["IV", "I", "V", "vi"], "IV-I-V-vi"),
    (["V", "vi", "IV", "I"], "V-vi-IV-I"),
    (["I", "vi", "IV", "V"], "I-vi-IV-V"),
]


@dataclass
class Chord:
    root: int
    minor: bool
    seconds: float
    start: float

    @property
    def name(self) -> str:
        return NOTE_NAMES[self.root] + ("m" if self.minor else "")


def _roman(chord: Chord, key_root: int, key_minor: bool) -> str:
    """The chord's function in the key, or its name when it has none."""
    degree = (chord.root - key_root) % 12
    table = MINOR_DEGREES if key_minor else MAJOR_DEGREES
    numeral = table.get(degree)
    if numeral is None:
        # Chromatic. Naming it by interval is more use than calling it
        # "non-diatonic", because the interval is the thing that tells you
        # what it is doing.
        return f"({chord.name})"
    # Case carries quality in roman numerals, so a chord whose quality
    # disagrees with the scale is worth showing as it was actually played.
    expected_minor = numeral.islower()
    if chord.minor != expected_minor:
        return numeral.upper() if not chord.minor else numeral.lower()
    return numeral


def progression(chords: list[Chord], key_root: int, key_minor: bool) -> list[str]:
    return [_roman(c, key_root, key_minor) for c in chords]


def _significant(raw_chords: list[dict], min_seconds: float = 0.6) -> list[Chord]:
    """Drops the passing detections nobody would call a chord change."""
    out: list[Chord] = []
    for entry in raw_chords:
        root = int(entry.get("root", -1))
        if root < 0:
            continue
        seconds = float(entry.get("seconds", 0.0))
        if seconds < min_seconds:
            continue
        out.append(Chord(root, bool(entry.get("minor")), seconds, float(entry.get("start", 0.0))))
    return out


def _cycle_matches(numerals: list[str], pattern: list[str]) -> bool:
    """Whether the progression is this pattern, allowing for where it starts."""
    if len(numerals) < len(pattern):
        return False
    window = numerals[: len(pattern)]
    for shift in range(len(pattern)):
        if window == pattern[shift:] + pattern[:shift]:
            return True
    return False


def judge(harmony: dict) -> list[dict]:
    """Notes about the harmony. Same shape as the co-producer's other notes."""
    notes: list[dict] = []
    key_root = int(harmony.get("keyRoot", -1))
    if key_root < 0:
        return notes

    key_minor = bool(harmony.get("minor"))
    confidence = float(harmony.get("confidence", 0.0))
    key_name = f"{NOTE_NAMES[key_root]} {'minor' if key_minor else 'major'}"

    chords = _significant(harmony.get("chords") or [])
    if not chords:
        return notes

    numerals = progression(chords, key_root, key_minor)
    unique = []
    for numeral in numerals:
        if not unique or unique[-1] != numeral:
            unique.append(numeral)

    def note(id_: str, severity: str, title: str, detail: str,
             evidence: str, action: Optional[dict] = None) -> dict:
        entry = {"id": id_, "severity": severity, "title": title,
                 "detail": detail, "evidence": evidence}
        if action:
            entry["action"] = action
        return entry

    # Always say what it heard. Even when there is nothing to criticise,
    # knowing the key is the single most useful thing for what you do next.
    notes.append(note(
        "key",
        "good" if confidence > 0.7 else "info",
        f"You're in {key_name}",
        "Everything below is relative to that." if confidence > 0.7
        else "Though not strongly - the chords are ambiguous enough that this "
             "is a best guess.",
        f"{' '.join(c.name for c in chords[:8])} · confidence {confidence:.2f}",
    ))

    distinct = {n for n in unique}

    # ---- the ones worth saying out loud ---------------------------------

    if len(distinct) == 1:
        notes.append(note(
            "static_harmony", "warn",
            "One chord, the whole way through",
            "Nothing moves harmonically, so every bar lands in the same place. "
            "Even one chord change gives the ear somewhere to go - the easiest "
            "is the fourth or the fifth.",
            f"{chords[0].name} for {sum(c.seconds for c in chords):.0f}s",
            _write_action(key_root, key_minor, "suggest"),
        ))
    elif len(distinct) == 2 and len(chords) >= 4:
        notes.append(note(
            "two_chords", "info",
            "Two chords, back and forth",
            "That is a real choice and plenty of records do it - but if the "
            "track feels like it is not going anywhere, this is why.",
            " ".join(unique[:4]),
        ))

    for pattern, label in AXIS_SETS:
        if _cycle_matches(unique, pattern):
            notes.append(note(
                "axis_progression", "info",
                "That's the axis progression",
                f"{label} is the four chords behind a huge share of the last "
                "twenty years of pop. It works, which is why. If you want the "
                "same lift without the familiarity, try swapping the last chord "
                "for a borrowed iv, or delaying the vi by a bar.",
                f"heard as {' '.join(unique[:4])} in {key_name}",
                _write_action(key_root, key_minor, "alternative"),
            ))
            break

    chromatic = [n for n in unique if n.startswith("(")]
    if not chromatic and len(distinct) >= 3:
        notes.append(note(
            "all_diatonic", "info",
            "Everything is in the key",
            "No borrowed or chromatic chords anywhere. Nothing wrong with that, "
            "but one chord from outside is the cheapest way to make a "
            "progression sound considered rather than default - a secondary "
            f"dominant ({NOTE_NAMES[(key_root + 2) % 12]} major going to the V) "
            "is the usual first try.",
            " ".join(unique[:6]),
        ))
    elif chromatic:
        notes.append(note(
            "chromatic", "good",
            "There's something from outside the key",
            "That is the part that will make this sound deliberate.",
            f"{', '.join(chromatic[:3])} against {key_name}",
        ))

    # Harmonic rhythm: how long each chord sits. Four bars a chord is a
    # choice; it is also the most common reason a loop feels slow.
    average = sum(c.seconds for c in chords) / len(chords)
    if average > 6.0 and len(distinct) > 1:
        notes.append(note(
            "slow_harmony", "info",
            "The chords change slowly",
            f"About {average:.0f} seconds a chord. If the track drags, halving "
            "that is a bigger change than anything you can do to the mix.",
            f"{len(chords)} changes over {sum(c.seconds for c in chords):.0f}s",
        ))

    return notes


def _write_action(key_root: int, key_minor: bool, kind: str) -> dict:
    """An alternative progression, in the key that is actually playing.

    Written into the channel rack as notes, so it is a thing you can hear
    and edit rather than a sentence telling you what to play.
    """
    if key_minor:
        # i - VI - III - VII: the minor-key workhorse, and it goes somewhere.
        degrees = [(0, True), (8, False), (3, False), (10, False)]
        label = "i-VI-III-VII"
    elif kind == "alternative":
        # vi - IV - I - V reordered into something with a stronger pull:
        # I - iii - IV - iv borrows the minor fourth at the end.
        degrees = [(0, False), (4, True), (5, False), (5, True)]
        label = "I-iii-IV-iv"
    else:
        degrees = [(0, False), (5, False), (9, True), (7, False)]
        label = "I-IV-vi-V"

    chords = []
    for semitones, minor in degrees:
        root = (key_root + semitones) % 12
        chords.append({
            "name": NOTE_NAMES[root] + ("m" if minor else ""),
            "root": root,
            "minor": minor,
        })

    return {
        "kind": "write_progression",
        "label": f"Write {label} into the piano roll",
        "params": {"chords": chords, "label": label},
    }
