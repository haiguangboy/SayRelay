import subprocess


def copy_text(text):
    subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=text.encode(),
                   check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title, body, *, persistent=False):
    options = ["--urgency=critical", "--expire-time=0"] if persistent else []
    subprocess.Popen(["notify-send", "--app-name=语音输入", "--icon=audio-input-microphone",
                      "--hint=string:x-canonical-private-synchronous:voice-input", *options, title, body],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
