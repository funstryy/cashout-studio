# Installing Cashout Studio

This is the guide for the person installing the studio, not for the person
building it. If you are building the installer, read `README.md` instead.

---

## You do not need Python

Or git, or Visual Studio, or a compiler, or a package manager, or an account.

This matters enough to say plainly, because the studio is built with those
things and people reasonably assume they need them. They do not. The
installer is one ordinary Windows program, and everything the studio needs
is either inside it or downloaded by it:

| Thing people expect to need | Do you need it? |
|---|---|
| Python | No. The studio carries its own, and so does the installer. |
| git | No. |
| Visual Studio or build tools | No. |
| An internet connection | Only if you install the AI engines. The DAW works offline. |
| An account or licence key | No. There is no sign-in anywhere in the product. |
| Administrator rights | No. It installs into your own user folder. |

The one thing you do need is an up-to-date graphics driver, and only for the
AI engines. See **If something goes wrong** below.

---

## What you need

- **Windows 10 (64-bit) or Windows 11.** 32-bit Windows will not run it.
- **A graphics card with a current driver.** AMD, NVIDIA or Intel are all
  fine; the engines use Vulkan, not CUDA. The studio was built and measured
  on a Radeon RX 5600 XT with 6 GB, so that is the realistic floor rather
  than a minimum nobody tested.
- **Disk space**, which depends entirely on what you install:

| What you install | Space |
|---|---:|
| The studio on its own (DAW, mixer, plugin host, mastering) | about 1.5 GB |
| Plus stem separation | about 2 GB |
| Plus voice cloning | about 7 GB |
| Plus music generation | about 17 GB |
| Everything, including ACE-Step | about 30 GB |

You do not have to decide now. Running the installer again later adds
whatever you skipped, and it will not re-download what you already have.

---

## Step 1: get the file

You need exactly one file:

```
CashoutStudio-Setup-1.0.0.exe
```

About 192 MB. There is nothing else to download by hand.

If you want to check that the file arrived intact, open PowerShell in the
folder you saved it to and run:

```powershell
Get-FileHash .\CashoutStudio-Setup-1.0.0.exe -Algorithm SHA256
```

Compare what it prints against `CashoutStudio-Setup-1.0.0.exe.sha256`,
which is published next to the installer and contains nothing but that
line. They should match exactly, ignoring upper and lower case.

If they do not match, the download was corrupted or the file was tampered
with. Download it again rather than running it.

---

## Step 2: Windows will try to stop you, once

Double-click the file and you will probably see a blue box:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognised app from starting.

Click **More info**, then **Run anyway**.

This is not a virus warning and Windows is not saying it found anything. It
is saying the file is not signed with a code-signing certificate, which
costs a few hundred dollars a year from a certificate authority. Every
unsigned installer gets this, and it goes away for everyone once enough
people have run a signed build.

The honest way to satisfy yourself is the checksum in Step 1: it tells you
the file is byte-for-byte the one that was published, which is the thing a
signature would also tell you.

If your antivirus quarantines the file instead, it is doing the same thing
for the same reason. Restore it from quarantine, or add the download folder
to its exclusions, and run it again.

---

## Step 3: the wizard

Six pages. Most people can press Next through all of them.

1. **Welcome.** Nothing to decide.
2. **Licence.** Read it, accept it.
3. **Install location.** The default is
   `C:\Users\<you>\AppData\Local\Programs\Cashout Studio`, inside your own
   user folder, which is why no administrator password is asked for. You can
   change it. If you plan to install the AI engines, put it on a drive with
   room for them: the weights land under the install folder.
4. **Components.** This is the only page worth stopping on:

   | Component | Download | What it gives you |
   |---|---:|---|
   | Cashout Studio | included | The DAW, mixer, plugin host, mastering, AI co-producer |
   | Stem separation | 486 MB | Splitting a song into vocals, drums, bass and the rest |
   | Voice cloning and conversion | 5.2 GB | The Voices page |
   | Music generation (YuE2) | 10.3 GB | The Beats page |
   | ACE-Step text-to-music | builds locally | The AI Music page and Voice Lab training |
   | Node.js LTS | small | Only needed to rebuild the app from source. Leave it off. |

   **If this is your first time, take stem separation and nothing else.**
   It is half a gigabyte, it finishes in a couple of minutes, and it is
   enough to see whether you like the studio. Adding music generation later
   is the same installer again.

   The wizard checks that the drive has room before it starts, so it will
   refuse a 10 GB download onto a 4 GB drive instead of failing halfway.

5. **Downloading.** A real progress bar, and Cancel really cancels. If the
   download is interrupted, by a cancel or a dropped connection or a reboot,
   running the installer again resumes from where it stopped rather than
   starting over.
6. **Finished.** Tick the box to launch it.

---

## Step 4: the first launch

In order:

1. The intro animation plays. Click anywhere or press any key to skip it,
   or tick "Don't show this again" if you never want to see it. The small
   readout in the corner is real: it is checking that the backend answers
   and that the audio engine is present.
2. The licence agreement, once per machine.
3. **Choose a profile.** Type a name and press Create. A profile keeps one
   person's tracks and projects separate from another's on the same
   computer. It is not a password, and it is not an account: anyone using
   this computer can pick any profile.
4. A short tutorial appears, and a different one on each of your first four
   launches. They are skippable and there is a "Don't show tutorials" link
   if you would rather explore.

Then press **DAW** in the sidebar and **New project**.

---

## If something goes wrong

**"Windows protected your PC"** is Step 2 above. It is expected.

**"Vulkan is not available"** on the wizard's warning page, or the AI pages
saying the engine will not start. Your graphics driver is out of date or was
installed without the Vulkan runtime. Get the current driver directly from
AMD, NVIDIA or Intel rather than from Windows Update, which often ships an
older one. The studio's DAW, mixer, plugins and mastering all work without
Vulkan; only the model-driven features need it.

**The download stalls or fails.** Close the installer and run it again. It
resumes. If it repeatedly stops at the same point, a firewall or a corporate
proxy is likely blocking `huggingface.co`.

**"Not enough disk space"** before the download starts. That is the check
working. Either free up space, choose fewer components, or reinstall to a
different drive.

**The studio opens but the engine lamps stay dark.** That is normal. The
models are not loaded until something needs them, and loading one takes a
minute or two. Click a lamp in the sidebar to start it and watch it go
amber, then green.

**The app forgot my profile.** This happens when something else on the
machine is using port 9000, so the studio had to fall back to another one
and Windows treats that as a different site. Close any second copy of the
studio and start it again.

**Clicks and dropouts while playing.** Open the Audio engine panel in the
DAW's right-hand rail and raise the buffer size. There is a fuller answer in
the studio's own Help page.

Anything else: the studio's **Help** tab has 38 answers covering the DAW,
the AI tools, the audio engine, collaboration and privacy, and its search
looks inside the answers as well as the questions.

---

## Adding engines later, and removing them

Run the same `CashoutStudio-Setup-1.0.0.exe` again and choose the components
you want. Anything already downloaded is left alone.

To uninstall: **Settings > Apps > Installed apps > Cashout Studio**, or the
uninstaller in the install folder.

**Uninstalling removes the program and the model weights. It does not touch
your music.** Projects, tracks and stems live in the `data` folder inside
the install directory, so copy that folder somewhere safe first if you are
uninstalling for any reason other than reinstalling in the same place.

---

## Where things are, if you ever need to look

| What | Where |
|---|---|
| The program | `%LOCALAPPDATA%\Programs\Cashout Studio` |
| Your projects, tracks and stems | `...\Cashout Studio\data` |
| Logs, when something has gone wrong | `...\Cashout Studio\logs` |
| Model weights | `...\Cashout Studio\engines` |

Paste `%LOCALAPPDATA%\Programs\Cashout Studio` into the address bar of any
Explorer window to get there.

---

## What the studio sends, and to whom

Nothing, with two exceptions you switch on yourself.

Every model runs on your own graphics card. Your audio is never uploaded for
generation, separation, re-voicing, mastering or analysis. There is no
telemetry, no crash reporting and no account.

The exceptions:

- **Treblo**, on its own page, is an external song-generation API. It does
  nothing until you paste in your own key, and it is labelled as external
  everywhere it appears.
- **Spotify**, if you connect it in Voice Lab, is used to read genre tags for
  captioning your own files. No audio is sent to it; no API returns audio
  from Spotify to anybody.

During installation the only thing contacted is Hugging Face, to download
the model weights you asked for.
