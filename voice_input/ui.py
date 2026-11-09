import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from .audio import sources
from .delivery import copy_text

CSS = b"""
window { background: #f5f5f3; }
.title { font-size: 25px; font-weight: bold; color: #202a27; }
.subtitle { color: #5d6863; }
.status { background: #e3eee7; border-radius: 12px; padding: 16px; color: #23583b; }
.recording { background: #f9e3df; color: #8f3026; }
.record-button { background: #236249; color: white; padding: 10px; font-weight: bold; }
textview text { background: white; color: #24312a; }
"""


class Window(Gtk.Window):
    def __init__(self, controller):
        super().__init__(title="语音输入")
        self.controller = controller
        self.set_default_size(560, 590)
        self.set_icon_name("audio-input-microphone")
        self.connect("delete-event", self._hide)
        self.last_seen = None
        self.changing = False
        self.provider = Gtk.CssProvider()
        self.provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self.provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_border_width(24)
        self.add(content)
        title = Gtk.Label(label="把话说出来，接着工作。", xalign=0)
        title.get_style_context().add_class("title")
        content.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label="本地识别 · 当前输入框 · 说完后可校对", xalign=0)
        subtitle.get_style_context().add_class("subtitle")
        content.pack_start(subtitle, False, False, 0)
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_line_wrap(True)
        self.status_label.get_style_context().add_class("status")
        content.pack_start(self.status_label, False, False, 0)
        shortcuts = Gtk.Label(label="Ctrl+Alt+空格  开始／结束     Ctrl+Alt+Shift+空格  取消", xalign=0)
        content.pack_start(shortcuts, False, False, 0)

        grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        self.model = Gtk.ComboBoxText()
        self.model.append("small", "small · 标准档")
        self.model.append("base", "base · 快速档")
        self.source = Gtk.ComboBoxText()
        self.input_mode = Gtk.ComboBoxText()
        self.input_mode.append("local", "本机麦克风")
        self.input_mode.append("remote", "小米遥控器麦克风")
        self.source.append("", "跟随系统默认麦克风")
        try:
            for name, description in sources():
                self.source.append(name, description)
        except Exception:
            pass
        self.output = Gtk.ComboBoxText()
        for key, label in [("paste", "粘贴到应用 · Ctrl+V"), ("terminal", "粘贴到终端 · Ctrl+Shift+V"),
                           ("clipboard", "只复制，由我手动粘贴")]:
            self.output.append(key, label)
        self.punctuation = Gtk.CheckButton(label="自动添加中文标点")
        self.punctuation.connect("toggled", self._punctuation_changed)
        for row, (label, widget) in enumerate([("输入来源", self.input_mode), ("模型", self.model),
                                             ("本机设备", self.source), ("输出", self.output)]):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
        content.pack_start(grid, False, False, 0)
        self.remote_label = Gtk.Label(xalign=0)
        self.remote_label.set_line_wrap(True)
        content.pack_start(self.remote_label, False, False, 0)
        self.remote_enable_button = Gtk.Button(label="启用遥控器按键（首次需要系统授权）")
        self.remote_enable_button.set_no_show_all(True)
        self.remote_enable_button.connect("clicked", lambda *_: self._action("enable-remote-buttons"))
        content.pack_start(self.remote_enable_button, False, False, 0)
        content.pack_start(self.punctuation, False, False, 0)
        for widget, key in [(self.input_mode, "input_mode"), (self.model, "model"),
                            (self.source, "source"), (self.output, "output")]:
            widget.connect("changed", self._configure, key)

        advanced = Gtk.Expander(label="识别方式与常用词")
        advanced_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        advanced_grid.set_margin_top(8)
        self.decoding = Gtk.ComboBoxText()
        self.decoding.append("fast", "快速识别")
        self.decoding.append("careful", "精细识别 · 等待稍长")
        self.decoding.connect("changed", self._configure, "decoding")
        advanced_grid.attach(Gtk.Label(label="识别方式", xalign=0), 0, 0, 1, 1)
        advanced_grid.attach(self.decoding, 1, 0, 2, 1)
        self.vocabulary = Gtk.Entry()
        self.vocabulary.set_placeholder_text("例如：Vance, Washington D.C., Codex")
        self.vocabulary.set_tooltip_text("用逗号分隔少量常用词，作为听写提示；不会强制替换正文。")
        self.vocabulary.set_max_length(400)
        self.vocabulary.set_hexpand(True)
        self.vocabulary.connect("activate", self._save_vocabulary)
        self.vocabulary_button = Gtk.Button(label="保存")
        self.vocabulary_button.connect("clicked", self._save_vocabulary)
        advanced_grid.attach(Gtk.Label(label="常用词", xalign=0), 0, 1, 1, 1)
        advanced_grid.attach(self.vocabulary, 1, 1, 1, 1)
        advanced_grid.attach(self.vocabulary_button, 2, 1, 1, 1)
        advanced.add(advanced_grid)
        content.pack_start(advanced, False, False, 0)
        self.last_vocabulary = None

        permission = Gtk.Box(spacing=12)
        self.enable_button = Gtk.Button(label="启用自动粘贴")
        self.enable_button.connect("clicked", lambda *_: self._action("enable-paste"))
        permission.pack_start(self.enable_button, False, False, 0)
        self.permission_label = Gtk.Label(xalign=0)
        self.permission_label.set_line_wrap(True)
        permission.pack_start(self.permission_label, True, True, 0)
        content.pack_start(permission, False, False, 0)

        self.record_button = Gtk.Button(label="开始录音")
        self.record_button.get_style_context().add_class("record-button")
        self.record_button.connect("clicked", self._record)
        content.pack_start(self.record_button, False, False, 0)
        transcript_title = Gtk.Label(label="最近一次识别", xalign=0)
        content.pack_start(transcript_title, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(110)
        self.text = Gtk.TextView()
        self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text.set_left_margin(12)
        self.text.set_right_margin(12)
        self.text.set_top_margin(10)
        self.text.set_bottom_margin(10)
        scroll.add(self.text)
        content.pack_start(scroll, True, True, 0)
        buttons = Gtk.Box(spacing=10)
        copy_button = Gtk.Button(label="复制文字")
        copy_button.connect("clicked", self._copy)
        hide_button = Gtk.Button(label="收起窗口")
        hide_button.connect("clicked", lambda *_: self.hide())
        quit_button = Gtk.Button(label="退出服务")
        quit_button.connect("clicked", lambda *_: self._action("quit"))
        for button in [copy_button, hide_button, quit_button]:
            buttons.pack_start(button, False, False, 0)
        content.pack_start(buttons, False, False, 0)
        hint = Gtk.Label(label="语音键录音；Home 打开／关闭概览，左右选应用，确定进入；平时确定键为 Enter。", xalign=0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("subtitle")
        content.pack_start(hint, False, False, 0)
        GLib.timeout_add(250, self.refresh)
        self.refresh()

    def _hide(self, *_):
        self.hide()
        return True

    def _action(self, action, **values):
        try:
            return self.controller.dispatch({"action": action, **values})
        except Exception as error:
            self.controller.message = str(error)
            return {"ok": False, "error": str(error)}

    def _configure(self, widget, key):
        if not self.changing and widget.get_active_id() is not None:
            self._action("configure", values={key: widget.get_active_id()})

    def _punctuation_changed(self, widget):
        if not self.changing:
            self._action("configure", values={"punctuation": widget.get_active()})

    def _save_vocabulary(self, *_):
        self._action("configure", values={"vocabulary": self.vocabulary.get_text().strip()})

    def _record(self, *_):
        if self.controller.state == "error":
            self._action("retry-load")
        else:
            self._action("toggle")
            if self.controller.state == "recording":
                self.hide()

    def _copy(self, *_):
        buffer = self.text.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        if text:
            try:
                copy_text(text)
                self.controller.message = "已复制窗口中的文字"
            except Exception as error:
                self.controller.message = str(error)

    def refresh(self):
        status = self.controller.status()
        recording = status["state"] == "recording"
        remote = status["config"]["input_mode"] == "remote"
        remote_status = status["remote"]
        battery = remote_status.get("battery")
        voice_message = ("遥控器语音通道已连接" if remote_status["state"] == "ready" and
                         not remote_status["buttons"]["ready"] else remote_status["message"])
        self.remote_label.set_text((voice_message +
                                    (f" · 电量 {battery}%" if battery is not None else "") + "\n" +
                                    (remote_status["setup_message"] if remote_status["setup_running"] else
                                     remote_status["buttons"]["message"])) if remote else "")
        self.remote_enable_button.set_visible(remote)
        self.remote_enable_button.set_sensitive(remote and not remote_status["buttons"]["ready"] and
                                                not remote_status["setup_running"])
        self.remote_enable_button.set_label("等待系统设备授权…" if remote_status["setup_running"] else
                                            "遥控器按键已启用" if remote_status["buttons"]["ready"] else
                                            "启用遥控器按键（首次需要系统授权）")
        self.status_label.set_text((f"● 录音中  {status['recording_seconds']:.0f} 秒\n" if recording else "") + status["message"])
        style = self.status_label.get_style_context()
        (style.add_class if recording else style.remove_class)("recording")
        self.record_button.set_label("停止并转写" if recording else "重试加载模型" if status["state"] == "error"
                                     else "请收起窗口，在目标输入框按住遥控器语音键" if remote else "开始录音")
        self.record_button.set_sensitive(status["state"] in {"recording", "error"} or
                                         (status["state"] == "idle" and not remote))
        self.permission_label.set_text(status["keyboard_message"])
        self.enable_button.set_label("恢复自动粘贴" if status.get("keyboard_remembered") else "启用自动粘贴")
        self.enable_button.set_sensitive(not status["keyboard_ready"] and not status["keyboard_connecting"])
        self.changing = True
        for widget, key in [(self.input_mode, "input_mode"), (self.model, "model"), (self.source, "source"), (self.output, "output"),
                            (self.decoding, "decoding")]:
            widget.set_active_id(status["config"][key])
            widget.set_sensitive(status["state"] == "idle")
        self.source.set_sensitive(status["state"] == "idle" and not remote)
        self.punctuation.set_active(status["config"]["punctuation"])
        self.punctuation.set_sensitive(status["state"] == "idle")
        if status["config"]["vocabulary"] != self.last_vocabulary:
            self.last_vocabulary = status["config"]["vocabulary"]
            self.vocabulary.set_text(self.last_vocabulary)
        self.vocabulary.set_sensitive(status["state"] == "idle")
        self.vocabulary_button.set_sensitive(status["state"] == "idle")
        self.changing = False
        if status["last_text"] != self.last_seen:
            self.text.get_buffer().set_text(status["last_text"])
            self.last_seen = status["last_text"]
        return GLib.SOURCE_CONTINUE
