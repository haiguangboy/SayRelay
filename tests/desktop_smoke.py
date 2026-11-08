"""Manual desktop integration check. Opens its own input; never sends Return."""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_input.cli import request

EXPECTED = "语音输入粘贴测试 Voice Input 123"
ROOT = Path(__file__).resolve().parent.parent


def main():
    result = {"tested_at": datetime.now().astimezone().isoformat(), "ok": False}
    deadline = time.monotonic() + 110
    print("Waiting for Ubuntu keyboard authorization…", flush=True)
    while time.monotonic() < deadline:
        status = request({"action": "status"})
        if status["keyboard_ready"] and status["state"] == "idle":
            break
        time.sleep(0.3)
    else:
        raise RuntimeError("系统键盘授权尚未就绪，请在应用中启用后再运行此测试")
    if status["config"]["output"] != "paste":
        raise RuntimeError("请先将输出选为应用 Ctrl+V")
    previous = None
    types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3)
    mime = next((t for t in types.stdout.splitlines() if t.startswith("text/plain")), None)
    if mime:
        old = subprocess.run(["wl-paste", "--no-newline", "--type", mime], capture_output=True, timeout=3)
        if old.returncode == 0:
            previous = (mime, old.stdout)
    window = Gtk.Window(title="语音输入 · 粘贴验证")
    window.set_default_size(520, 160)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_border_width(20)
    box.pack_start(Gtk.Label(label="正在验证系统粘贴，请保持此测试窗口的焦点…"), False, False, 0)
    entry = Gtk.Entry()
    box.pack_start(entry, False, False, 0)
    window.add(box)
    result["backend"] = type(window.get_display()).__name__
    started = time.monotonic()
    sent = False
    sent_at = None

    def tick():
        nonlocal sent, sent_at
        if sent and entry.get_text() == EXPECTED:
            result.update(ok=True, observed=entry.get_text(), elapsed_seconds=time.monotonic() - started)
            Gtk.main_quit()
            return False
        if time.monotonic() - started > 8:
            result["error"] = "没有确认测试输入框收到文字"
            Gtk.main_quit()
            return False
        if not sent and time.monotonic() - started > 0.6 and window.is_active() and entry.has_focus():
            response = request({"action": "insert", "text": EXPECTED})
            result["job_id"] = response["job_id"]
            sent = True
            sent_at = time.monotonic()
        elif sent and time.monotonic() - sent_at > 0.18 and not window.is_active():
            request({"action": "cancel"})
            result["error"] = "测试窗口失去焦点，已取消待执行粘贴"
            Gtk.main_quit()
            return False
        return True

    window.show_all()
    window.present()
    entry.grab_focus()
    GLib.timeout_add(30, tick)
    try:
        Gtk.main()
    finally:
        if result.get("job_id"):
            result["job"] = request({"action": "job", "id": result["job_id"]})["job"]
        window.destroy()
        if previous:
            subprocess.run(["wl-copy", "--type", previous[0]], input=previous[1], check=True, timeout=5)
        name = "verification-paste-" + result["backend"].lower() + ".json"
        (ROOT / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
