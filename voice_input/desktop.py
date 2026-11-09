"""GNOME overview and application switching using existing desktop interfaces.

No Shell Eval, extensions, window-title access, or global keybinding changes.
OverviewActive is checked before each overview key so closing it with the mouse
or keyboard does not leave a stale remote navigation mode behind.
"""

import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gdk, Gtk

SHELL = "org.gnome.Shell"
SHELL_PATH = "/org/gnome/Shell"
PROPERTIES = "org.freedesktop.DBus.Properties"


class DesktopNotReady(RuntimeError):
    pass


class GnomeDesktop:
    def __init__(self, keyboard):
        self.keyboard = keyboard
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.settings = Gio.Settings.new("org.gnome.desktop.wm.keybindings")
        self.selection_started = False
        self.transition_until = 0
        self.confirm_until = 0
        self.subscription = self.bus.signal_subscribe(
            SHELL, PROPERTIES, "PropertiesChanged", SHELL_PATH, SHELL,
            Gio.DBusSignalFlags.NONE, self._changed, None)

    def _changed(self, _bus, _sender, _path, _interface, _signal, parameters, _data):
        interface, changed, invalidated = parameters.unpack()
        if interface == SHELL and ("OverviewActive" in changed or "OverviewActive" in invalidated):
            self.selection_started = False

    def overview_active(self):
        value = self.bus.call_sync(
            SHELL, SHELL_PATH, PROPERTIES, "Get",
            GLib.Variant("(ss)", (SHELL, "OverviewActive")),
            GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        if not isinstance(value, bool):
            raise RuntimeError("无法确认 GNOME 应用概览状态")
        if not value:
            self.selection_started = False
        return value

    def toggle_overview(self):
        visible = not self.overview_active()
        self.bus.call_sync(
            SHELL, SHELL_PATH, PROPERTIES, "Set",
            GLib.Variant("(ssv)", (SHELL, "OverviewActive", GLib.Variant("b", visible))),
            None, Gio.DBusCallFlags.NONE, 1000, None)
        self.selection_started = False
        # No delayed keys: a press during the animation is rejected, never queued.
        self.transition_until = time.monotonic() + 0.35
        self.confirm_until = self.transition_until
        return {"action": "overview", "message": "已请求打开应用概览，左右键选择，确定键进入" if visible else
                "已请求关闭应用概览"}

    def _focus_windows(self):
        if time.monotonic() < self.transition_until:
            raise DesktopNotReady("应用概览正在切换，请稍后再按")
        if not self.selection_started:
            # GNOME 46 overviewControls sends Tab into the window picker.
            self.keyboard.shortcut(0xFF09)
            self.selection_started = True
        if not self.overview_active():
            raise DesktopNotReady("应用概览已关闭，请重新选择应用")

    def move(self, direction):
        if direction not in {"left", "right"}:
            raise ValueError("无效切换方向")
        if time.monotonic() < self.transition_until:
            raise DesktopNotReady("应用概览正在切换，请稍后再按")
        if self.overview_active():
            self._focus_windows()
            self.keyboard.shortcut(0xFF51 if direction == "left" else 0xFF53)
            return {"action": "overview_" + direction, "message": "已在应用概览中向" +
                    ("左" if direction == "left" else "右") + "选择窗口"}
        binding = "switch-applications-backward" if direction == "left" else "switch-applications"
        accelerators = self.settings.get_strv(binding)
        if not accelerators:
            raise RuntimeError("系统的应用切换快捷键未设置，请在 Ubuntu 键盘设置中启用")
        key, mask = Gtk.accelerator_parse(accelerators[0])
        mapping = [(Gdk.ModifierType.CONTROL_MASK, 0xFFE3),
                   (Gdk.ModifierType.MOD1_MASK, 0xFFE9),
                   (Gdk.ModifierType.SUPER_MASK, 0xFFEB),
                   (Gdk.ModifierType.SHIFT_MASK, 0xFFE1)]
        supported = sum(int(flag) for flag, _keysym in mapping)
        if not key or int(mask) & ~supported:
            raise RuntimeError("暂不支持当前应用切换快捷键的修饰键，请使用 Ctrl、Alt、Super 或 Shift")
        self.keyboard.shortcut(key, [keysym for flag, keysym in mapping if mask & flag])
        # A second, very fast confirm must not submit text in the newly focused app.
        self.confirm_until = time.monotonic() + 0.35
        return {"action": "switch_" + direction, "message": "已发送系统应用切换快捷键"}

    def confirm_overview(self):
        if time.monotonic() < self.confirm_until:
            raise DesktopNotReady("正在切换应用，请稍后再按确定键")
        if not self.overview_active():
            return False
        self._focus_windows()
        self.keyboard.enter()
        self.confirm_until = time.monotonic() + 0.5
        return True

    def close(self):
        if self.subscription is not None:
            self.bus.signal_unsubscribe(self.subscription)
            self.subscription = None
