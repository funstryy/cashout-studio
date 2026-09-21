Cashout Studio 1.0.0, the first release anyone else can install.

A complete music production studio that runs on one ordinary Windows
machine: a multitrack DAW with a native C++ audio engine, VST3 hosting,
mastering measured to broadcast specification, an AI co-producer, stem
separation and music generation. Every model runs on your own graphics
card.

## Install

Download **CashoutStudio-Setup-1.0.0.exe** and run it. That is the whole
process.

You do not need Python, git, Visual Studio, a package manager, an account
or administrator rights. The installer contains everything and downloads
only the model weights you ask for.

Windows will show **"Windows protected your PC"** the first time, because
the installer is not signed with a code-signing certificate. Click **More
info**, then **Run anyway**. Verify the download instead if you would
rather not take that on trust:

```powershell
Get-FileHash .\CashoutStudio-Setup-1.0.0.exe -Algorithm SHA256
```

```
1d3c8689e50527cb1eb41bb167a797399ad52f2d923d3bb364d18050c39f62e2
```

**First time in, take stem separation and nothing else.** It is 486 MB,
finishes in a couple of minutes, and is enough to decide whether you like
the studio. Running the installer again later adds the rest without
re-downloading anything.

Full instructions, including what to do when something goes wrong, are in
[INSTALL.md](INSTALL.md) and in the studio's own Help tab.

## What you need

- Windows 10 64-bit or Windows 11
- A graphics card with a current driver. AMD, NVIDIA or Intel: the engines
  use Vulkan, not CUDA. Built and measured on a Radeon RX 5600 XT with
  6 GB, which is the realistic floor.
- 1.5 GB for the studio alone, up to about 30 GB with every engine

## Highlights

- **2.67 ms** measured output latency, zero dropouts, on a real-time thread
- **VST3 plugins** live in the signal path
- **Mastering** to ITU-R BS.1770-4 and EBU R128, true peak at 4x oversampling
- **An AI co-producer** that shows the measurement behind every note it makes
- **Live collaboration** over a LAN or VPN
- **Nothing leaves your machine**, apart from the optional Treblo API

## Known limitations

- Windows only.
- Unsigned installer, hence the SmartScreen warning above.
- YuE2 weights are CC BY-NC 4.0, so tracks generated through YuE2 cannot be
  used commercially without separate permission from the rights holder.
- ACE-Step runs on CPU on AMD hardware and is slow there.

Full list in the [changelog](CHANGELOG.md).

## Credits

Cashout Studio is built by Alexander Gary Hubel Michael Di Ienno, Yurii
Grechko, Antonio Aguiar and the rest of Cashout Pt.5, in Fair Lawn, New
Jersey.

