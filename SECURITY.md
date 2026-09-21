# Security

## What this software is, in security terms

Cashout Studio runs entirely on one machine. The backend binds to
`127.0.0.1` and is not reachable from the network. There is no account
system, no server we operate, and no telemetry. That removes most of the
categories a report would normally fall into, and leaves a few that are
real.

## Where the risk actually is

**The collaboration listener.** Hosting a session opens a second HTTP
listener that *is* reachable from your network or VPN. It is deliberately
narrow: it serves the song, its mixdown and the stems of its tracks,
matched against explicit path patterns, and refuses everything else. The
audio engine is never exposed on it. A way to reach anything outside that
list, or to reach it without the session code, is a vulnerability and we
want to hear about it.

**Stored keys.** The Treblo API key and the Spotify client ID are held by
the backend rather than the page, so they do not appear in a screenshot or
a network pane. They are stored in plain text and readable by anyone using
the same computer. This is documented rather than fixed, because profiles
are not a security boundary and pretending otherwise would be worse. If
you find a way for a *remote* party to read them, that is a
vulnerability.

**Plugin hosting.** Loading a VST3 runs third-party native code in the
engine process, by design. A malicious plugin can do anything your user
account can. That is true of every DAW and is not a bug here, but a way to
make the studio load a plugin the user did not choose would be.

**The installer.** It downloads model weights over HTTPS from Hugging
Face. A way to make it fetch and execute something else is a
vulnerability.

## Reporting

Open a [private security advisory][advisory] on this repository. If that
is not available to you, open a normal issue that says only that you have
found a security problem and how to reach you, without the details.

Please include what you did, what happened, and what you expected. A proof
of concept helps enormously and does not need to be polished.

[advisory]: https://github.com/funstryy/cashout-studio/security/advisories/new

## What to expect

This is a small project maintained by a few people around other
commitments, so an honest timeline rather than a flattering one:

- An acknowledgement within about a week.
- An assessment of whether it is a real issue within about two.
- A fix when there is one, credited to you unless you would rather not be.

We will not take legal action against anyone reporting in good faith.

## Out of scope

- Anything requiring an attacker to already have access to the user's
  Windows account. They have already won.
- The absence of a code-signing certificate, and the SmartScreen warning
  that follows from it. Known, documented in `INSTALL.md`, and a question
  of money rather than engineering.
- Profiles not being password protected. That is stated plainly in the
  interface and in the FAQ.
- Vulnerabilities in the models or their upstream repositories. Report
  those to their authors; tell us too, so we can pin or drop a version.
