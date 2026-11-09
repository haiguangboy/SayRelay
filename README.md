# SayRelay

**Offline dictation for Ubuntu. Speak, transcribe, paste.**

[![Tests](https://github.com/haiguangboy/SayRelay/actions/workflows/tests.yml/badge.svg)](https://github.com/haiguangboy/SayRelay/actions/workflows/tests.yml)

[简体中文](README.zh-CN.md) · [Model setup](docs/MODELS.md) · [Changelog](CHANGELOG.md) · [Roadmap](docs/PRODUCT_PLAN.md)

SayRelay turns microphone audio into text and pastes it into the focused input field. Use it with Codex, editors, browsers, and terminal applications that accept clipboard paste.

Speech recognition runs locally. Chinese output is converted to Simplified Chinese and receives punctuation from a local model. No cloud transcription account or API key is required.

**Status: 0.1.0 prototype, with experimental remote input.** Tested on Ubuntu 24.04 with GNOME 46 and Wayland. Choose the computer's microphone or Xiaomi Bluetooth Remote 2 Pro in settings. Real remote recording, local transcription, and paste into a Wayland test input have passed; the user also confirmed dictation into Codex. The settings window currently uses Chinese labels.

## What works today

- Global shortcuts to start, stop, or cancel recording.
- Local Whisper inference on CPU, with `small` and optional `base` models.
- Simplified Chinese conversion and optional Chinese punctuation.
- Paste into applications with `Ctrl+V`, terminals with `Ctrl+Shift+V`, or copy only.
- Remembered desktop keyboard permission and restoration after a service restart.
- Visible warnings and preserved text when automatic paste fails.
- A GTK settings window, CLI, and local Unix socket for integrations.
- A saved input-source switch, BlueZ remote discovery, battery status, and ATVV audio decoding.

SayRelay pastes text without pressing Enter. Choose the input field before recording and keep it focused until delivery completes.

## Install from source

You need an Ubuntu 24.04 GNOME graphical session, Python 3.12, and a microphone. Internet access is needed for the initial dependencies and model downloads; dictation uses the downloaded models locally.

Clone this repository into a permanent location and enter the source directory:

```bash
git clone https://github.com/haiguangboy/SayRelay.git
cd SayRelay
```

Then install the system dependencies:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-3.0 pipewire-bin pulseaudio-utils wl-clipboard libnotify-bin curl
```

Create the environment using Ubuntu's system Python so it can access PyGObject:

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e .
```

Download the `small` speech model and punctuation model using the [model setup guide](docs/MODELS.md), and verify their checksums. Default model files total roughly 562 MB and are downloaded separately from the source code. The optional `base` model adds roughly 148 MB.

Install the user service, application launcher, and shortcuts from the graphical session:

```bash
.venv/bin/python install.py
```

Search for **语音输入** in the application menu to open the settings window. Closing the window leaves the service running; it starts with the graphical session. The installed commands reference this source directory, so keep it in place. Source installation is currently the supported distribution method; no deb or AppImage is provided.

## First dictation

1. Open the settings window and click **启用自动粘贴** (Enable automatic paste).
2. In Ubuntu's system dialog, keep **Remember this decision** selected and allow keyboard control. The system may describe this as remote desktop access; SayRelay requests keyboard control for pasting.
3. Return to your application and click its text input field.
4. Press `Ctrl+Alt+Space`, speak, and press the shortcut again to transcribe and paste.
5. Check the result and send it yourself when ready.

| Action | Shortcut |
| --- | --- |
| Start or finish recording | `Ctrl+Alt+Space` |
| Cancel the current recording or pending result | `Ctrl+Alt+Shift+Space` |

Remembered permission is restored automatically when supported by the desktop portal. Revoking it, an invalid restore token, or choosing not to remember may require approval again. If keyboard permission is unavailable before recording, SayRelay asks you to restore it and explicitly reports that recording has not started. After approval, return to the target field and press the shortcut again.

If permission is lost after recording starts, the transcript stays available in the window and clipboard, with a notification explaining how to paste manually. Notification visibility depends on your desktop settings. Copy-only mode does not require keyboard control.

## Output modes and CLI

The command is installed at `~/.local/bin/voice-input`; use that full path if the directory is not on your `PATH`.

```bash
voice-input show
voice-input status
voice-input toggle
voice-input cancel

# Select the computer microphone or the paired Xiaomi remote
voice-input configure --input-mode local
voice-input configure --input-mode remote

# Applications, terminals, or clipboard only
voice-input configure --output paste
voice-input configure --output terminal
voice-input configure --output clipboard

# Transcribe a local 16 kHz, mono, 16-bit PCM WAV without changing the clipboard
voice-input transcribe /absolute/path/sample.wav --wait

# Deliver text from another local tool using the selected output mode
voice-input insert 'Text from another program'
```

The public name is SayRelay; the current command, service, Python package, and configuration paths retain the `voice-input` / `voice_input` names.

The [Chinese guide](README.zh-CN.md) includes model options, vocabulary hints, and the JSON socket interface. The socket is restricted to the same user and does not expose an HTTP service.

## Xiaomi remote input (experimental)

Pair **Xiaomi Bluetooth Remote 2 Pro** in Ubuntu's Bluetooth settings, then select **输入来源 → 小米遥控器麦克风** in SayRelay. On first use, click **启用遥控器按键** and authenticate in Ubuntu's system dialog. Wait until the voice channel and buttons are both ready, hide the settings window, focus the destination input, hold the remote's voice button, speak, and release to transcribe and paste. Set up keyboard paste permission before the first recording. `Ctrl+Alt+Shift+Space` cancels; the regular recording shortcut also controls the selected input source.

The current adapter targets **RC003, firmware 2671, ATVV 1.0 / ADPCM 16 kHz**. It discovers characteristic paths on each connection, displays battery level, and retries connection failures with a bounded delay. Remote audio stays in memory. Disconnects, missing audio synchronization, and stalled streams discard the incomplete recording. Switching to the computer microphone stops SayRelay's remote notifications without unpairing the remote. Your saved local microphone selection is retained.

The remote is not a PipeWire audio source. On this Ubuntu setup, its voice key arrives as native `F5` events, without the expected ATVV start request. SayRelay therefore reads press/release events from the exact Bluetooth vendor/product (`2717:32B8`), verifies the opened device's identity again, and grabs only that remote to suppress its native key effects. While remote input is selected, other remote buttons are consumed without actions; the computer's keyboard is unaffected. Switching to local input or exiting releases the grab.

The one-time system dialog installs `70-sayrelay-remote.rules`, granting the active desktop user access only to the RC003 input device through udev/logind. The app then runs without root, without membership of the broad `input` group, and without an additional Python Bluetooth package. If graphical authentication is unavailable, run the helper from a terminal:

```bash
sudo /usr/bin/python3 tools/remote_input_access.py
# Optional removal; reconnect the remote afterward to revoke the old device ACL.
sudo /usr/bin/python3 tools/remote_input_access.py --remove
```

A measured 6.255-second remote recording (417 packets) took 1.49 seconds to transcribe, including punctuation, and about 1.90 seconds from release to text appearing in the test input. The test read the actual input contents and confirmed they matched the transcript, with no native F5 events reaching the window. This is one local measurement with the `small` model, not an accuracy benchmark. Other firmware, the non-Pro model, and sleep/wake behavior still require validation.

## Privacy and permissions

- Recording, transcription, Simplified Chinese conversion, and punctuation run on this computer.
- Temporary microphone recordings are deleted after normal completion, cancellation, or service exit. This is not a persistent recording archive.
- The most recent text and up to 20 task results remain in process memory. The desktop clipboard, and any clipboard manager you use, can retain delivered text.
- Keyboard restore permission is stored in the user's configuration directory with file mode `0600`. The service does not return its token through the status interface.
- Only installing system packages requires administrator privileges. Recording and pasting run as the current user.

See [SECURITY.md](SECURITY.md) for details. Avoid submitting private recordings, transcripts, or authorization tokens with bug reports.

## Current limits

- English names, company names, and Chinese/English mixed speech can be inaccurate. Vocabulary hints and careful decoding are experimental and can also cause omissions.
- Punctuation does not correct recognition errors; review text before sending it.
- Automatic paste depends on focus and the target application's shortcuts. SayRelay does not verify that a particular application received the text.
- Terminal output must currently be selected manually. Recording defaults to a 120-second limit.
- Service restart recovery has been verified locally. Full computer restart, suspend/resume, long idle periods, other desktop environments, and other hardware still need testing.
- Remote input remains experimental; packaged installers and automatic updates are not implemented.

## Development and validation

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The current suite has 62 tests. Without the local punctuation model, 57 run and 5 model-dependent tests are skipped. With the model installed, all 62 have passed locally. Remote tests cover the codec, notifications, stale callbacks, device identity checks, press/release and repeat handling, disconnect cancellation and the shared recognition pipeline. Real Wayland and XWayland input-field paste checks also passed before remote integration; these checks do not measure speech recognition accuracy.

Desktop smoke tests open their own input window and require an authorized graphical session:

```bash
GDK_BACKEND=wayland .venv/bin/python tests/desktop_smoke.py
GDK_BACKEND=x11 .venv/bin/python tests/desktop_smoke.py
# With the remote selected, ready and paste authorized, speak into its own test window:
GDK_BACKEND=wayland .venv/bin/python tests/remote_smoke.py
```

Keep the test window focused. These tests paste into that window without pressing Enter.

Further documentation is currently in Chinese: [architecture](docs/ARCHITECTURE.md), [validation record](VALIDATION.md), [contributing](CONTRIBUTING.md), and [release process](docs/RELEASE.md). The first GitHub Actions run passed on Ubuntu 24.04 with the original suite of 25 tests and 5 model-dependent skips. The current suite counts are listed above. Desktop integration still requires a real graphical session.

## Uninstall

```bash
.venv/bin/python install.py --uninstall
```

This removes the application's user service, launcher, and shortcuts. Source files, models, and user configuration are retained.

## Roadmap and acknowledgments

Next steps are longer remote dictation trials, sleep/reconnect testing, better mixed-language recognition, and easier installation with verified model downloads. See the [product plan](docs/PRODUCT_PLAN.md).

The project was inspired by [Axonkey](https://github.com/leowzz/axonkey), [Mi-Ao](https://github.com/fanxeon/mi-ao), and [SayAll](https://github.com/GetSayAll/remote-mic-app-windows). ATVV protocol handling and the ADPCM decoder are adapted from Mi-Ao under MIT; its copyright and full license are included in [licenses/mi-ao-MIT.txt](licenses/mi-ao-MIT.txt). The BlueZ, Ubuntu UI, recognition and delivery integration are implemented in this project.

Application code is licensed under [MIT](LICENSE). Dependencies and model weights retain their upstream licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Model weights are not included in this repository.
