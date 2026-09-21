#!/usr/bin/env python3
"""Fails if the two locale files have drifted apart.

Every string in the interface exists twice, once per language, and the
pages that read a list by index (the FAQ, the About credits, the
tutorials) render a blank card rather than falling back when an entry is
missing on one side. That is invisible until somebody switches language,
so it is worth a machine noticing instead.

This parses the files rather than pattern-matching them. The first
attempt counted braces with a regex and a stack, reported that 1031 keys
agreed, and went on reporting it with an entry deliberately deleted: the
brace that closed an array element looked exactly like the brace that
closed an object. A check that cannot fail is worse than no check,
because it is believed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "frontend/src/locales/en.ts"
RU = ROOT / "frontend/src/locales/ru.ts"


class Parser:
    """Enough of the TypeScript object literal syntax these files use."""

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def error(self, what: str) -> None:
        line = self.s.count("\n", 0, self.i) + 1
        raise SystemExit(f"parse error on line {line}: {what}")

    def skip(self) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n,":
                self.i += 1
            elif self.s.startswith("//", self.i):
                self.i = self.s.find("\n", self.i) + 1 or len(self.s)
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i)
                self.i = len(self.s) if end < 0 else end + 2
            else:
                return

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                out.append(self.s[self.i:self.i + 2])
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        self.error("unterminated string")
        return ""

    def key(self) -> str:
        self.skip()
        if self.s[self.i] in "'\"":
            name = self.string()
        else:
            start = self.i
            while self.s[self.i] not in ":" and not self.s[self.i].isspace():
                self.i += 1
            name = self.s[start:self.i]
        self.skip()
        if self.s[self.i] != ":":
            self.error(f"expected ':' after {name!r}")
        self.i += 1
        return name

    def value(self):
        self.skip()
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "'\"`":
            self.string()
            return "str"
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in ",}]\n":
            self.i += 1
        literal = self.s[start:self.i].strip()
        return "lit" if literal else self.error("empty value")

    def obj(self) -> dict:
        self.i += 1  # {
        out: dict = {}
        while True:
            self.skip()
            if self.i >= len(self.s):
                self.error("unterminated object")
            if self.s[self.i] == "}":
                self.i += 1
                return out
            # Two statements on purpose. In `out[self.key()] = self.value()`
            # Python evaluates the right-hand side first, so the value was
            # being read before its own key and every pair came out shifted
            # by one. The parser still ran, and still reported agreement.
            name = self.key()
            out[name] = self.value()

    def arr(self) -> list:
        self.i += 1  # [
        out: list = []
        while True:
            self.skip()
            if self.i >= len(self.s):
                self.error("unterminated array")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    start = text.index("{", text.index("export default"))
    p = Parser(text[start:])
    return p.obj()


def compare(a, b, path: str, problems: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() - b.keys():
            problems.append(f"missing from ru.ts: {path}{k}")
        for k in b.keys() - a.keys():
            problems.append(f"missing from en.ts: {path}{k}")
        for k in sorted(a.keys() & b.keys()):
            compare(a[k], b[k], f"{path}{k}.", problems)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            problems.append(
                f"{path.rstrip('.')}: en has {len(a)} entries, ru has {len(b)}")
        else:
            for n, (x, y) in enumerate(zip(a, b)):
                compare(x, y, f"{path}{n}.", problems)
    elif type(a) is not type(b):
        problems.append(f"{path.rstrip('.')}: en is {type(a).__name__}, ru is {type(b).__name__}")


def main() -> int:
    en, ru = load(EN), load(RU)
    problems: list[str] = []
    compare(en, ru, "", problems)

    if problems:
        print("The locale files have drifted:\n")
        for p in problems[:40]:
            print(f"  {p}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1

    def count(node) -> int:
        if isinstance(node, dict):
            return sum(count(v) for v in node.values())
        if isinstance(node, list):
            return sum(count(v) for v in node)
        return 1

    print(f"locales agree: {count(en)} strings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
