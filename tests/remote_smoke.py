"""Manual real-remote end-to-end check in an isolated GTK input field.

Requires remote input mode, paste output and an authorized keyboard session.
Speak a non-private test sentence. No raw audio is saved and no Return is sent.
"""

import json
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_input.cli import request

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait for manual speech (1–3600)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1–3600 seconds")
    status = request({"action": "status"})
    if (status["state"] != "idle" or status["config"]["input_mode"] != "remote" or
            status["config"]["output"] != "paste" or not status["keyboard_ready"] or
            not status["remote"]["ready"]):
        raise RuntimeError("请先选择遥控器麦克风、应用粘贴，并等待连接与键盘授权就绪")
    previous = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=3)
    result = {"tested_at": datetime.now().astimezone().isoformat(), "ok": False, "key_events": []}
    initial_job = (status.get("job") or {}).get("id")
    window = Gtk.Window(title="SayRelay · 遥控器语音测试")
    window.set_default_size(620, 260)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    box.set_border_width(24)
    label = Gtk.Label(label="请点选下方输入框，按住遥控器语音键，说：\n今天我们测试小米遥控器语音输入。\n松开语音键，等待文字自动出现在下方。", xalign=0)
    box.pack_start(label, False, False, 0)
    entry = Gtk.Entry()
    box.pack_start(entry, False, False, 0)
    window.add(box)
    started = time.monotonic()
    received_at = None
    recording_seen = False
    job_id = None
    finished = False

    def key(_widget, event):
        result["key_events"].append(Gdk.keyval_name(event.keyval))
        return False

    def close(*_):
        Gtk.main_quit()
        return True

    window.connect("delete-event", close)
    window.connect("key-press-event", key)

    def tick():
        nonlocal received_at, recording_seen, job_id, finished
        s = request({"action": "status"})
        job = s.get("job") or {}
        if s["state"] == "recording" and job.get("id") != initial_job:
            if not recording_seen:
                print("Recording from remote…", flush=True)
            recording_seen = True
            label.set_text(f"正在使用遥控器录音 · {s['recording_seconds']:.1f} 秒\n"
                           f"已收到 {s['remote']['audio_packets']} 个音频包，松开语音键后转写。")
        if recording_seen and job.get("id") != initial_job:
            job_id = job.get("id")
            if s["state"] in {"transcribing", "delivering"} and received_at is None:
                received_at = time.monotonic()
            if not window.is_active() and s["state"] in {"recording", "transcribing", "delivering"}:
                request({"action": "cancel"})
                result["error"] = "测试窗口失去焦点，已取消本次交付"
                Gtk.main_quit()
                return False
            if s["state"] == "idle":
                text = job.get("text", "")
                matched = bool(text) and entry.get_text() == text and job.get("delivery") == "paste_sent"
                if matched or job.get("state") in {"error", "cancelled"} or not text:
                    result.update(ok=matched, job=job, remote=s["remote"],
                                  pasted_text=entry.get_text(), recording_seen=recording_seen,
                                  release_to_paste_seconds=(time.monotonic() - received_at if received_at else None))
                    finished = True
                    label.set_text("测试通过：遥控器录音、转写、自动粘贴均已完成。" if matched else
                                   "测试未完成：" + job.get("message", ""))
                    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                    GLib.timeout_add_seconds(3, lambda: (Gtk.main_quit(), False)[1])
                    return False
        if time.monotonic() - started > args.timeout:
            result["error"] = "等待遥控器测试超时"
            Gtk.main_quit()
            return False
        return True

    window.show_all()
    window.present()
    entry.grab_focus()
    GLib.timeout_add(100, tick)
    try:
        Gtk.main()
    finally:
        if not finished and job_id:
            request({"action": "cancel"})
        window.destroy()
        if previous.returncode == 0:
            subprocess.run(["wl-copy"], input=previous.stdout, check=True, timeout=5)
        (ROOT / "verification-remote-desktop.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["ok"]:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
