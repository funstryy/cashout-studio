# Contributing to Cashout Studio

Thanks for looking. This is a small project built by a few people, so the
process is short.

## Before you open a pull request

**Open an issue first for anything larger than a fix.** Not bureaucracy:
the audio engine, the workspace layout and the model orchestration all
have constraints that are not obvious from the code, and it is unpleasant
to find that out after writing a week of it.

**Small fixes need no issue.** A crash, a wrong label, a broken link, a
translation that reads badly: just send it.

## Setting up

You need Windows 10 or 11, Python 3.11, Node 20 and Visual Studio 2022
with the C++ workload. Then:

```powershell
.\setup_prereqs.ps1     # build tools and ffmpeg
.\setup_models.ps1      # clones and builds the engines
.\dev.bat               # backend on :9000, frontend on :5173
```

The native audio engine is separate and optional for most work:

```powershell
cd engine
.\build.ps1
```

Nothing in the studio requires the engine to be built. It falls back to
the browser audio path, which is slower and cannot host live plugins, but
everything else behaves the same.

## The house style

The code is commented more heavily than most projects, and deliberately.
The rule is that a comment explains **why**, not what, and earns its place
by saying something the code cannot:

- a constant that came from a measurement records the measurement
- a workaround records what broke, and how it was observed to break
- a decision that looks wrong records why the obvious thing was worse

If a comment restates the line below it, delete the comment.

Other conventions:

- **Measure before you claim.** Anything the interface tells the user
  about their audio (loudness, true peak, key, chords) must come from a
  real measurement against a named standard, and the interface should show
  the number.
- **Errors quote what failed.** Do not paraphrase an exception into a
  calm sentence. The message the user sees should be one they can act on.
- **English and Russian move together.** Every string lives in
  `frontend/src/locales/en.ts` and `ru.ts`, and both change in the same
  commit. Same for `README.md` and `README.ru.md`.
- **No em dashes in user-facing English copy.** Colons, commas and full
  stops do the same jobs.

## Before you push

```powershell
cd frontend
npm run build          # must pass
npx vue-tsc --noEmit -p tsconfig.app.json
```

There is no test suite worth the name yet. If you are touching the DSP in
`engine/src/dsp.cpp` or `mastering.cpp`, verify against the specification
rather than against the previous output: a loudness figure that matches
what the code used to produce tells you nothing about whether either is
right.

## What is unlikely to be merged

- A dependency that pulls in a runtime the installer would have to ship.
- Anything that sends user audio off the machine by default.
- A feature gated behind an account, a key or a subscription.
- A larger minimum VRAM requirement. Six gigabytes is the target.

## Licence

Contributions are accepted under the MIT licence, the same as the rest of
the project. Cashout Studio is a derivative of
[Remiqora](https://github.com/inikolax/remiqora) by Nikolay Cherkashin and
that copyright notice stays in every copy, as the licence requires.
