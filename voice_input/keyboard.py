"""Keyboard-only xdg-desktop-portal session, without root or device permissions."""

import uuid

from gi.repository import Gio, GLib

from .config import CONFIG_DIR

DEST = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.RemoteDesktop"
AUTH_FILE = CONFIG_DIR / "keyboard-restore-token"


class PortalKeyboard:
    def __init__(self, changed):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = None
        self.pending_session = None
        self.connecting = False
        self.changed = changed
        self.pending = {}
        self.closed_subscription = None
        self.restore_token = self._read_restore_token()
        self.version = 1

    @staticmethod
    def _read_restore_token():
        try:
            return str(uuid.UUID(AUTH_FILE.read_text().strip()))
        except (OSError, ValueError):
            return ""

    def _store_restore_token(self, token):
        if token:
            token = str(uuid.UUID(token))
            CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = AUTH_FILE.with_suffix(".tmp")
            temporary.touch(mode=0o600, exist_ok=True)
            temporary.chmod(0o600)
            temporary.write_text(token + "\n")
            temporary.replace(AUTH_FILE)
        else:
            AUTH_FILE.unlink(missing_ok=True)
        self.restore_token = token

    def _request(self, method, signature, args, completed):
        token = "voice_" + uuid.uuid4().hex
        options = args[-1]
        options["handle_token"] = GLib.Variant("s", token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        state = {"done": False}

        def finish(error, result=None):
            if state["done"]:
                return
            state["done"] = True
            self.bus.signal_unsubscribe(state["subscription"])
            GLib.source_remove(state["timeout"])
            self.pending.pop(path, None)
            completed(error, result)

        def response(_bus, _sender, _path, _interface, _signal, parameters, _data):
            code, result = parameters.unpack()
            finish(None if code == 0 else RuntimeError("系统键盘授权未完成，可重新启用"), result)

        state["subscription"] = self.bus.signal_subscribe(
            DEST, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, response, None)

        def timed_out():
            self.bus.call(DEST, path, "org.freedesktop.portal.Request", "Close", None,
                          None, Gio.DBusCallFlags.NONE, 2000, None, None, None)
            finish(RuntimeError("等待系统授权超时，请重新启用自动粘贴"))
            return GLib.SOURCE_REMOVE

        state["timeout"] = GLib.timeout_add_seconds(120, timed_out)
        self.pending[path] = state

        def replied(connection, call_result, _data):
            try:
                connection.call_finish(call_result)
            except GLib.Error as error:
                finish(error)

        self.bus.call(DEST, PATH, INTERFACE, method, GLib.Variant(signature, tuple(args)),
                      GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, 10000,
                      None, replied, None)

    def connect(self):
        if self.session or self.connecting:
            return
        self.connecting = True
        self.changed("正在恢复自动粘贴授权…" if self.restore_token else
                     "请在系统窗口允许键盘控制，并保留“记住此决定”")

        try:
            result = self.bus.call_sync(
                DEST, PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (INTERFACE, "version")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 3000, None)
            self.version = result.unpack()[0]
        except GLib.Error:
            self.version = 1

        def failed(error):
            self.connecting = False
            self.close()
            self.changed(str(error))

        def created(error, result):
            if error:
                failed(error)
                return
            session = result["session_handle"]
            self.pending_session = session

            def selected(error, _result):
                if error:
                    failed(error)
                    return

                def started(error, result):
                    if error:
                        failed(error)
                        return
                    if not result.get("devices", 0) & 1:
                        failed(RuntimeError("系统没有授予键盘控制权限"))
                        return
                    self.session = session
                    self.pending_session = None
                    self.connecting = False
                    self.closed_subscription = self.bus.signal_subscribe(
                        DEST, "org.freedesktop.portal.Session", "Closed", session, None,
                        Gio.DBusSignalFlags.NONE, self._closed, None)
                    try:
                        self._store_restore_token(result.get("restore_token", ""))
                    except (OSError, ValueError):
                        self.changed("自动粘贴已启用，但未能保存授权；下次重启可能需要再次允许")
                    else:
                        self.changed("自动粘贴已启用，授权已记住" if self.restore_token else
                                     "自动粘贴已启用，仅本次运行有效")

                self._request("Start", "(osa{sv})", [session, "", {}], started)

            options = {"types": GLib.Variant("u", 1)}
            if self.version >= 2:
                options["persist_mode"] = GLib.Variant("u", 2)
                if self.restore_token:
                    options["restore_token"] = GLib.Variant("s", self.restore_token)
                    # Portal tokens are single use. Start returns the replacement.
                    try:
                        self._store_restore_token("")
                    except OSError as error:
                        failed(error)
                        return
            self._request("SelectDevices", "(oa{sv})", [session, options], selected)

        self._request("CreateSession", "(a{sv})", [{"session_handle_token":
                      GLib.Variant("s", "voice_" + uuid.uuid4().hex)}], created)

    def _closed(self, *_args):
        self.session = None
        self.connecting = False
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription)
            self.closed_subscription = None
        self.changed("键盘会话已结束，下次录音前会恢复授权" if self.restore_token else
                     "键盘授权已结束；请重新启用自动粘贴")

    def key(self, keysym, pressed):
        if not self.session:
            raise RuntimeError("请先在语音输入窗口中启用自动粘贴")
        try:
            self.bus.call_sync(DEST, PATH, INTERFACE, "NotifyKeyboardKeysym",
                               GLib.Variant("(oa{sv}iu)", (self.session, {}, keysym, int(pressed))),
                               None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            self.close()
            self.changed("键盘会话已失效；文字已保留，下次录音前会重新连接")
            raise

    def paste(self, terminal=False):
        modifiers = [0xFFE3] + ([0xFFE1] if terminal else [])  # Control_L, Shift_L
        pressed = []
        try:
            for key in modifiers + [ord("v")]:
                self.key(key, True)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                try:
                    self.key(key, False)
                except Exception:
                    pass

    def enter(self):
        self.key(0xFF0D, True)  # Return, without Control or other modifiers.
        self.key(0xFF0D, False)

    def shortcut(self, keysym, modifiers=()):
        pressed = []
        try:
            for key in [*modifiers, keysym]:
                self.key(key, True)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                self.key(key, False)

    def close(self):
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription)
            self.closed_subscription = None
        for path, state in self.pending.items():
            state["done"] = True
            self.bus.signal_unsubscribe(state["subscription"])
            GLib.source_remove(state["timeout"])
            self.bus.call(DEST, path, "org.freedesktop.portal.Request", "Close", None,
                          None, Gio.DBusCallFlags.NONE, 2000, None, None, None)
        self.pending.clear()
        for session in {self.session, self.pending_session} - {None}:
            self.bus.call(DEST, session, "org.freedesktop.portal.Session", "Close",
                          None, None, Gio.DBusCallFlags.NONE, 3000, None, None, None)
        self.session = self.pending_session = None
        self.connecting = False
