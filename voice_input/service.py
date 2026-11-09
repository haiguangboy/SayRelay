import json
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from gi.repository import GLib

from .audio import Microphone, read_pcm
from .config import Config, ROOT, RUNTIME_DIR, SOCKET_PATH
from .delivery import copy_text, notify
from .keyboard import PortalKeyboard
from .recognizer import Recognizer
from .remote import RemoteMicrophone
from .remote_buttons import RemoteButtons


class Controller:
    def __init__(self):
        self.config = Config.load()
        self.state = "loading"
        self.message = "正在加载本地模型…"
        self.last_text = ""
        self.last_result = None
        self.recognizer = None
        self.microphone = Microphone()
        self.capture = None
        self.remote = RemoteMicrophone(self._remote_requested, self._remote_ended, self._remote_failed)
        self.remote_buttons = RemoteButtons(self._remote_requested, self._remote_ended, self._remote_failed)
        self.remote.button_control = True
        self.remote_setup_process = None
        self.remote_setup_message = ""
        self.jobs = OrderedDict()
        self.job = None
        self.cancelled = set()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recognition")
        self.keyboard_message = "尚未启用自动粘贴"
        self.keyboard = PortalKeyboard(self._keyboard_changed)
        self.window = None
        self.quitting = False
        if self.config.input_mode == "remote":
            self.remote.enable()
            self.remote_buttons.enable()
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
            self.message = (f"模型加载失败：{error}" if error else
                            "就绪，等待遥控器连接后按住语音键说话" if self.config.input_mode == "remote" else
                            "就绪，按 Ctrl+Alt+空格开始录音")

        self._run(lambda: Recognizer(config), completed)

    def status(self):
        elapsed = time.monotonic() - self.capture.started if self.state == "recording" else 0
        remote = self.remote.status()
        remote["buttons"] = self.remote_buttons.status()
        remote["ready"] = remote["ready"] and self.remote_buttons.ready
        remote["setup_running"] = self.remote_setup_process is not None
        remote["setup_message"] = self.remote_setup_message
        return {"ok": True, "state": self.state, "message": self.message,
                "config": asdict(self.config), "keyboard_ready": bool(self.keyboard.session),
                "keyboard_connecting": self.keyboard.connecting,
                "keyboard_remembered": bool(self.keyboard.restore_token),
                "keyboard_message": self.keyboard_message, "recording_seconds": elapsed,
                "remote": remote,
                "job": self.job, "last_text": self.last_text, "last_result": self.last_result}

    def _remote_requested(self):
        if self.config.input_mode != "remote" or self.quitting:
            return
        if self.state == "recording" and self.capture is self.remote:
            return
        if self.state != "idle":
            notify("尚未开始遥控器录音", "请等当前转写完成，再按住语音键")
            return
        try:
            self.start()
        except Exception as error:
            self.message = str(error)
            notify("遥控器未开始录音", self.message)

    def _remote_ended(self):
        if self.state == "recording" and self.capture is self.remote:
            try:
                self.stop()
            except Exception as error:
                self._remote_failed(str(error))

    def _remote_failed(self, message):
        if self.state == "recording" and self.capture is self.remote:
            self.remote.discard()
            self._finish(self.job, "error", message)
            notify("遥控器录音未完成", message, persistent=True)

    def _enable_remote_buttons(self):
        if self.remote_setup_process is not None:
            return {"ok": True, "pending": True}
        script = ROOT / "tools/remote_input_access.py"
        if not script.is_file():
            raise RuntimeError("设备授权脚本缺失，请使用完整源码安装 SayRelay")
        self.remote_setup_process = subprocess.Popen(
            ["pkexec", "/usr/bin/python3", str(script)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.remote_setup_message = "请在 Ubuntu 系统授权窗口输入密码，启用小米遥控器按键"

        def completed():
            process = self.remote_setup_process
            if process is None:
                return GLib.SOURCE_REMOVE
            if process.poll() is None:
                return GLib.SOURCE_CONTINUE
            stdout, stderr = process.communicate()
            self.remote_setup_process = None
            self.remote_setup_message = ("遥控器设备授权已完成，正在接通按键" if process.returncode == 0 else
                                         "设备授权未完成，可重试；也可查看文档中的 sudo 安装方式")
            if process.returncode != 0:
                notify("遥控器按键尚未启用", self.remote_setup_message, persistent=True)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(250, completed)
        return {"ok": True, "pending": True}

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
        capture = self.remote if self.config.input_mode == "remote" else self.microphone
        if capture is self.remote and self.remote.state != "ready":
            raise RuntimeError(self.remote.message)
        if self.config.output != "clipboard" and not self.keyboard.session:
            self.keyboard.connect()
            retry = "按住遥控器语音键" if capture is self.remote else "按 Ctrl+Alt+空格"
            self.message = f"自动粘贴尚未就绪，本次未开始录音；完成系统授权后请再次{retry}"
            notify("尚未开始录音：请先启用自动粘贴", self.message, persistent=True)
            return {"ok": True, "state": self.state, "recording_started": False,
                    "reason": "keyboard_authorization"}
        capture.start(self.config.source)
        self.capture = capture
        job = self._new_job(True)
        job["input_mode"] = self.config.input_mode
        self.state = "recording"
        self.message = ("正在使用遥控器录音，松开语音键结束；Ctrl+Alt+Shift+空格取消"
                        if capture is self.remote else
                        "正在录音，再按 Ctrl+Alt+空格结束；Ctrl+Alt+Shift+空格取消")
        notify("开始录音", "松开遥控器语音键结束并转写" if capture is self.remote else
               "再按 Ctrl+Alt+空格结束并转写")
        return {"ok": True, "job_id": job["id"], "state": self.state}

    def stop(self):
        if self.state != "recording":
            raise RuntimeError("当前没有录音")
        if self.capture is self.remote:
            # Freeze the PCM on the GLib thread, before any later BLE packets.
            try:
                pcm = self.remote.stop()
            except Exception as error:
                self._remote_failed(str(error))
                raise
            self._recognize(lambda: pcm)
        else:
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
        self.capture = None
        self.message = message

    def cancel(self):
        if self.state == "recording":
            self.capture.discard()
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
            capture = self.capture
            try:
                capture.check()
                if time.monotonic() - capture.started >= self.config.max_record_seconds:
                    self.stop()
            except Exception as error:
                capture.discard()
                if self.state == "recording":
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
        if action == "enable-remote-buttons":
            return self._enable_remote_buttons()
        if action == "configure":
            self._available()
            data = {**asdict(self.config), **request.get("values", {})}
            config = Config(**data).validate()
            reload_model = (config.model, config.threads) != (self.config.model, self.config.threads)
            switch_input = config.input_mode != self.config.input_mode
            if not config.model_path.joinpath("model.bin").is_file():
                raise ValueError("本地模型尚未安装")
            config.save()
            self.config = config
            if switch_input:
                if config.input_mode == "remote":
                    self.remote.enable()
                    self.remote_buttons.enable()
                else:
                    self.remote_buttons.close()
                    self.remote.close()
                self.message = ("已切换到遥控器麦克风" if config.input_mode == "remote" else
                                "已切换到本机麦克风，按 Ctrl+Alt+空格开始录音")
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
        self.remote_buttons.close()
        self.remote.close()
        if self.remote_setup_process and self.remote_setup_process.poll() is None:
            try:
                self.remote_setup_process.terminate()
            except (PermissionError, ProcessLookupError):
                # An already authenticated, bounded system setup may finish.
                pass
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
