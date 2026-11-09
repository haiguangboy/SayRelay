import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import RUNTIME_DIR, SOCKET_PATH


def request(payload, start=True):
    if start and not SOCKET_PATH.exists():
        subprocess.run(["systemctl", "--user", "start", "voice-input.service"], check=True, timeout=10)
        deadline = time.monotonic() + 8
        while not SOCKET_PATH.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(str(SOCKET_PATH))
        connection.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        with connection.makefile("rb") as stream:
            raw = stream.readline(1024 * 1024)
        result = json.loads(raw)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "服务错误"))
    return result


def serve(background=False):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
    from .service import ControlServer, Controller
    import threading

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (RUNTIME_DIR / "service.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not background:
            request({"action": "show"}, start=False)
        return
    GLib.set_prgname("voice-input")
    GLib.set_application_name("语音输入")
    controller = Controller()
    server = ControlServer(controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if not background:
        controller.dispatch({"action": "show"})
    for signum in [signal.SIGTERM, signal.SIGINT]:
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: (Gtk.main_quit(), False)[1])
    try:
        Gtk.main()
    finally:
        controller.close()
        server.shutdown()
        server.server_close()
        SOCKET_PATH.unlink(missing_ok=True)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="本地语音输入：Ctrl+Alt+空格开始／结束")
    commands = parser.add_subparsers(dest="command", required=True)
    daemon = commands.add_parser("serve", help="运行后台服务")
    daemon.add_argument("--background", action="store_true")
    for command in ["toggle", "start", "stop", "cancel", "status", "show", "enable-paste",
                    "enable-remote-buttons", "copy", "quit"]:
        commands.add_parser(command)
    transcribe = commands.add_parser("transcribe", help="转写 16 kHz 单声道 PCM WAV")
    transcribe.add_argument("path", type=Path)
    transcribe.add_argument("--deliver", action="store_true", help="同时交付到当前应用")
    transcribe.add_argument("--wait", action="store_true", help="等待并打印结果")
    insert = commands.add_parser("insert", help="把文字交付到当前输入框；- 从标准输入读取")
    insert.add_argument("text")
    config = commands.add_parser("configure")
    config.add_argument("--model", choices=["small", "base"])
    config.add_argument("--output", choices=["paste", "terminal", "clipboard"])
    config.add_argument("--source")
    config.add_argument("--input-mode", choices=["local", "remote"], help="本机麦克风或小米遥控器")
    config.add_argument("--threads", type=int)
    config.add_argument("--language", choices=["zh", "en", "auto"], help="主要语言")
    config.add_argument("--decoding", choices=["fast", "careful"], help="快速或精细识别")
    config.add_argument("--vocabulary", help="常用词提示，传空字符串可清除")
    config.add_argument("--punctuation", choices=["on", "off"], help="中文自动标点")
    job = commands.add_parser("job")
    job.add_argument("id")
    arguments = parser.parse_args()
    try:
        if arguments.command == "serve":
            serve(arguments.background)
            return
        payload = {"action": arguments.command}
        if arguments.command == "transcribe":
            payload.update(path=str(arguments.path.resolve()), deliver=arguments.deliver)
        elif arguments.command == "configure":
            payload["values"] = {key: value for key, value in vars(arguments).items()
                                 if key != "command" and value is not None}
            if "punctuation" in payload["values"]:
                payload["values"]["punctuation"] = arguments.punctuation == "on"
        elif arguments.command == "insert":
            payload["text"] = sys.stdin.read(16001) if arguments.text == "-" else arguments.text
        elif arguments.command == "job":
            payload["id"] = arguments.id
        result = request(payload)
        if arguments.command == "transcribe" and arguments.wait:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                result = request({"action": "job", "id": result.get("job_id", result.get("job", {}).get("id"))})
                if result["job"]["state"] in {"done", "error", "cancelled"}:
                    break
                time.sleep(0.15)
            else:
                raise TimeoutError("等待转写超时；可用 job 命令查看后台任务")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if (result.get("job") or {}).get("state") == "error":
            sys.exit(1)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
