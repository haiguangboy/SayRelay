import json
import os
import socket
import socketserver
import struct
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from gi.repository import GLib

from .audio import Microphone, read_pcm
from .config import Config, RUNTIME_DIR, SOCKET_PATH
from .delivery import copy_text, notify
from .keyboard import PortalKeyboard
from .recognizer import Recognizer


class Controller:
    def __init__(self):
        self.config = Config.load()
        self.state = "loading"
        self.message = "正在加载本地模型…"
        self.last_text = ""
        self.last_result = None
        self.recognizer = None
        self.microphone = Microphone()
        self.jobs = OrderedDict()
        self.job = None
        self.cancelled = set()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recognition")
        self.keyboard_message = "尚未启用自动粘贴"
        self.keyboard = PortalKeyboard(self._keyboard_changed)
        self.window = None
        self.quitting = False
        self._load()
        if self.config.output != "clipboard" and self.keyboard.restore_token:
            GLib.idle_add(self._restore_keyboard)
        GLib.timeout_add(250, self._tick)

    def _restore_keyboard(self):
        if not self.quitting:
            self.keyboard.connect()
        return GLib.SOURCE_REMOVE

    def _keyboard_changed(self, message):
        self.keyboard_message = message

    def _run(self, work, completed):
        future = self.worker.submit(work)

        def done(future):
            def finish():
                if not self.quitting:
                    try:
                        result = future.result()
                    except Exception as error:
                        completed(None, error)
                    else:
                        completed(result, None)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(finish)

        future.add_done_callback(done)

    def _load(self):
        self.state = "loading"
        self.message = f"正在加载 {self.config.model} 本地模型…"
        config = self.config

        def completed(recognizer, error):
            self.recognizer = recognizer
            self.state = "error" if error else "idle"
            self.message = f"模型加载失败：{error}" if error else "就绪，按 Ctrl+Alt+空格开始录音"

        self._run(lambda: Recognizer(config), completed)

    def status(self):
        elapsed = time.monotonic() - self.microphone.started if self.state == "recording" else 0
        return {"ok": True, "state": self.state, "message": self.message,
                "config": asdict(self.config), "keyboard_ready": bool(self.keyboard.session),
                "keyboard_connecting": self.keyboard.connecting,
                "keyboard_remembered": bool(self.keyboard.restore_token),
                "keyboard_message": self.keyboard_message, "recording_seconds": elapsed,
                "job": self.job, "last_text": self.last_text, "last_result": self.last_result}

    def _available(self):
        if self.state != "idle":
            raise RuntimeError("请等待当前操作完成" if self.state != "loading" else "正在加载模型，请稍候")

    def _new_job(self, deliver):
        job = {"id": uuid.uuid4().hex, "state": "recording", "text": "",
               "output": self.config.output if deliver else "none"}
        self.jobs[job["id"]] = job
        while len(self.jobs) > 20:
            self.jobs.popitem(last=False)
        self.job = job
        return job

    def start(self):
        self._available()
        if self.config.output != "clipboard" and not self.keyboard.session:
            self.keyboard.connect()
            self.message = "自动粘贴尚未就绪，本次未开始录音；完成系统授权后请再次按 Ctrl+Alt+空格"
            notify("尚未开始录音：请先启用自动粘贴", self.message, persistent=True)
            return {"ok": True, "state": self.state, "recording_started": False,
                    "reason": "keyboard_authorization"}
        self.microphone.start(self.config.source)
        job = self._new_job(True)
        self.state = "recording"
        self.message = "正在录音，再按 Ctrl+Alt+空格结束；Ctrl+Alt+Shift+空格取消"
        notify("开始录音", "再按 Ctrl+Alt+空格结束并转写")
        return {"ok": True, "job_id": job["id"], "state": self.state}

    def stop(self):
        if self.state != "recording":
            raise RuntimeError("当前没有录音")
        self._recognize(self.microphone.stop)
        notify("正在转写", "请保持目标输入框的焦点")
        return {"ok": True, "job_id": self.job["id"], "state": self.state}

    def _recognize(self, read_audio):
        job = self.job
        self.state = job["state"] = "transcribing"
        self.message = "正在本机转写…"
        language = self.config.language
        punctuation = self.config.punctuation
        decoding = self.config.decoding
        vocabulary = self.config.vocabulary

        def work():
            return self.recognizer.transcribe(read_audio(), language, punctuation=punctuation,
                                              decoding=decoding, vocabulary=vocabulary)

        self._run(work, lambda result, error: self._recognized(job, result, error))

    def _recognized(self, job, result, error):
        if job["id"] in self.cancelled:
            self.cancelled.discard(job["id"])
            self._finish(job, "cancelled", "已取消，未交付文字")
            return
        if error:
            self._finish(job, "error", str(error))
            notify("转写未完成", str(error))
            return
        job.update(result)
        self.last_result = result
        if not result["text"]:
            self._finish(job, "done", "未检测到可识别的语音，未交付文字")
            notify("没有识别到语音", "请检查麦克风选择、静音和输入音量")
            return
        self.last_text = result["text"]
        if job["output"] == "none":
            self._finish(job, "done", "文件转写完成")
            return
        self._deliver(job)

    def _deliver(self, job):
        try:
            copy_text(job["text"])
        except Exception as error:
            self._finish(job, "error", f"文字已识别，但复制失败：{error}")
            return
        if job["output"] == "clipboard" or not self.keyboard.session:
            job["delivery"] = "clipboard"
            message = "已复制，可手动粘贴"
            if job["output"] != "clipboard":
                shortcut = "Ctrl+Shift+V" if job["output"] == "terminal" else "Ctrl+V"
                message = f"录音已转写，未自动粘贴；文字已复制，请回到输入框按 {shortcut}"
            self._finish(job, "done", message)
            notify("未自动粘贴：文字已保留" if job["output"] != "clipboard" else "转写完成",
                   message, persistent=job["output"] != "clipboard")
            return
        self.state = job["state"] = "delivering"

        def paste():
            if self.quitting:
                return GLib.SOURCE_REMOVE
            if job["id"] in self.cancelled:
                self.cancelled.discard(job["id"])
                self._finish(job, "cancelled", "已取消粘贴；文字已在剪贴板")
                return GLib.SOURCE_REMOVE
            try:
                self.keyboard.paste(terminal=job["output"] == "terminal")
                job["delivery"] = "paste_sent"
                self._finish(job, "done", "已发送粘贴快捷键，请检查目标输入框")
            except Exception as error:
                job["delivery"] = "clipboard"
                self._finish(job, "done", f"自动粘贴不可用，文字已复制：{error}")
                shortcut = "Ctrl+Shift+V" if job["output"] == "terminal" else "Ctrl+V"
                notify("未自动粘贴：文字已保留", f"录音已转写，请回到输入框按 {shortcut}。下次录音前会检查授权。",
                       persistent=True)
            return GLib.SOURCE_REMOVE

        # Give the clipboard owner and released global shortcut modifiers time to settle.
        GLib.timeout_add(250, paste)

    def _finish(self, job, state, message):
        job["state"] = state
        job["message"] = message
        self.state = "idle"
        self.message = message

    def cancel(self):
        if self.state == "recording":
            self.microphone.discard()
            self._finish(self.job, "cancelled", "已取消录音")
        elif self.state in {"transcribing", "delivering"}:
            self.cancelled.add(self.job["id"])
            self.message = "正在取消，结果不会粘贴"
        else:
            return {"ok": True, "state": self.state}
        notify("已取消", "此次录音不会自动粘贴")
        return {"ok": True, "state": self.state}

    def _tick(self):
        if self.quitting:
            return GLib.SOURCE_REMOVE
        if self.state == "recording":
            try:
                self.microphone.check()
                if time.monotonic() - self.microphone.started >= self.config.max_record_seconds:
                    self.stop()
            except Exception as error:
                self.microphone.discard()
                self._finish(self.job, "error", str(error))
                notify("录音失败", str(error))
        return GLib.SOURCE_CONTINUE

    def dispatch(self, request):
        action = request.get("action")
        if action == "status":
            return self.status()
        if action == "toggle":
            return self.stop() if self.state == "recording" else self.start()
        if action == "start":
            return self.start()
        if action == "stop":
            return self.stop()
        if action == "cancel":
            return self.cancel()
        if action == "show":
            from .ui import Window
            if self.window is None:
                self.window = Window(self)
            self.window.show_all()
            self.window.present()
            return {"ok": True}
        if action == "enable-paste":
            self.keyboard.connect()
            return {"ok": True, "ready": bool(self.keyboard.session)}
        if action == "configure":
            self._available()
            data = {**asdict(self.config), **request.get("values", {})}
            config = Config(**data).validate()
            reload_model = (config.model, config.threads) != (self.config.model, self.config.threads)
            if not config.model_path.joinpath("model.bin").is_file():
                raise ValueError("本地模型尚未安装")
            config.save()
            self.config = config
            if reload_model:
                self.recognizer = None
                self._load()
            return {"ok": True, "config": asdict(config)}
        if action == "transcribe":
            self._available()
            path = Path(request["path"])
            if not path.is_absolute() or not path.is_file():
                raise ValueError("请提供现有 WAV 文件的绝对路径")
            self._new_job(bool(request.get("deliver", False)))
            self._recognize(lambda: read_pcm(path))
            return {"ok": True, "job_id": self.job["id"]}
        if action == "insert":
            self._available()
            text = request.get("text", "")
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError("文字应为 1–16000 个字符")
            job = self._new_job(True)
            job["text"] = self.last_text = text
            self._deliver(job)
            return {"ok": True, "job_id": job["id"]}
        if action == "job":
            job = self.jobs.get(request.get("id"))
            if job is None:
                raise ValueError("未找到任务，最多保留最近 20 项内存结果")
            return {"ok": True, "job": job}
        if action == "copy":
            if not self.last_text:
                raise ValueError("还没有识别文字")
            copy_text(self.last_text)
            return {"ok": True}
        if action == "retry-load":
            if self.state != "error":
                raise ValueError("仅在模型加载失败时重试")
            self._load()
            return {"ok": True}
        if action == "quit":
            from gi.repository import Gtk
            GLib.timeout_add(100, Gtk.main_quit)
            return {"ok": True}
        raise ValueError("未知命令")

    def close(self):
        self.quitting = True
        self.microphone.discard()
        self.keyboard.close()
        self.worker.shutdown(wait=False, cancel_futures=True)


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, controller):
        self.controller = controller
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        RUNTIME_DIR.chmod(0o700)
        SOCKET_PATH.unlink(missing_ok=True)
        super().__init__(str(SOCKET_PATH), Handler)
        SOCKET_PATH.chmod(0o600)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        _pid, uid, _gid = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            return
        try:
            data = self.rfile.readline(65537)
            if len(data) > 65536 or not data.endswith(b"\n"):
                raise ValueError("请求过大或不完整")
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError("请求应为 JSON 对象")
            response = {}
            done = threading.Event()

            def dispatch():
                try:
                    response.update(self.server.controller.dispatch(request))
                except Exception as error:
                    response.update(ok=False, error=str(error))
                finally:
                    done.set()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(dispatch)
            if not done.wait(10):
                raise TimeoutError("服务响应超时")
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        try:
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
        except (BrokenPipeError, ConnectionError):
            pass
