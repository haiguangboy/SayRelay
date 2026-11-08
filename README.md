# SayRelay

**Offline dictation for Ubuntu. Speak, transcribe, paste.**

[简体中文](README.zh-CN.md) · [Model setup](docs/MODELS.md) · [Changelog](CHANGELOG.md) · [Roadmap](docs/PRODUCT_PLAN.md)

SayRelay turns microphone audio into text and pastes it into the focused input field. Use it with Codex, editors, browsers, and terminal applications that accept clipboard paste.

Speech recognition runs locally. Chinese output is converted to Simplified Chinese and receives punctuation from a local model. No cloud transcription account or API key is required.

**Status: 0.1.0 prototype.** Tested on Ubuntu 24.04 with GNOME 46 and Wayland. The current input source is the computer's microphone. Xiaomi Bluetooth remote support is planned. The settings window currently uses Chinese labels.

## What works today

- Global shortcuts to start, stop, or cancel recording.
- Local Whisper inference on CPU, with `small` and optional `base` models.
- Simplified Chinese conversion and optional Chinese punctuation.
- Paste into applications with `Ctrl+V`, terminals with `Ctrl+Shift+V`, or copy only.
- Remembered desktop keyboard permission and restoration after a service restart.
- Visible warnings and preserved text when automatic paste fails.
- A GTK settings window, CLI, and local Unix socket for integrations.

SayRelay pastes text without pressing Enter. Choose the input field before recording and keep it focused until delivery completes.

## Install from source

You need an Ubuntu 24.04 GNOME graphical session, Python 3.12, and a microphone. Internet access is needed for the initial dependencies and model downloads; dictation uses the downloaded models locally.

Clone or extract this repository into a permanent location, open a terminal there, then install the system dependencies:

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

# Applications, terminals, or clipboard only
voice-input configure --output paste
voice-input configure --output terminal
voice-input configure --output clipboard

# Transcribe a local 16 kHz, mono, 16-bit PCM WAV without changing the clipboard
voice-input transcribe /absolute/path/sample.wav --wait

# Deliver text from another local tool using the selected output mode
voice-input insert 'Text from another program'
```

The proposed public name is SayRelay; the current command, service, Python package, and configuration paths retain the `voice-input` / `voice_input` names.

The [Chinese guide](README.zh-CN.md) includes model options, vocabulary hints, and the JSON socket interface. The socket is restricted to the same user and does not expose an HTTP service.

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
- Xiaomi remote input, packaged installers, and automatic updates are not implemented.

## Development and validation

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The current suite has 30 tests. Without the local punctuation model, 25 run and 5 model-dependent tests are skipped. With the model installed, all 30 have passed locally. Real Wayland and XWayland input-field paste checks also passed; these checks do not measure speech recognition accuracy.

Desktop smoke tests open their own input window and require an authorized graphical session:

```bash
GDK_BACKEND=wayland .venv/bin/python tests/desktop_smoke.py
GDK_BACKEND=x11 .venv/bin/python tests/desktop_smoke.py
```

Keep the test window focused. These tests paste into that window without pressing Enter.

Further documentation is currently in Chinese: [architecture](docs/ARCHITECTURE.md), [validation record](VALIDATION.md), [contributing](CONTRIBUTING.md), and [release process](docs/RELEASE.md). GitHub Actions is configured; remote execution has not yet been verified.

## Uninstall

```bash
.venv/bin/python install.py --uninstall
```

This removes the application's user service, launcher, and shortcuts. Source files, models, and user configuration are retained.

## Roadmap and acknowledgments

Next steps are Xiaomi Bluetooth Remote 2 Pro investigation and integration, better mixed-language recognition, and easier installation with verified model downloads. See the [product plan](docs/PRODUCT_PLAN.md).

The project was inspired by [Axonkey](https://github.com/leowzz/axonkey), [Mi-Ao](https://github.com/fanxeon/mi-ao), and [SayAll](https://github.com/GetSayAll/remote-mic-app-windows). It uses its own Ubuntu implementation; no application code from those projects was copied.

Application code is licensed under [MIT](LICENSE). Dependencies and model weights retain their upstream licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Model weights are not included in this repository.
