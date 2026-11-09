"""Desktop navigation protocol without switching the user's actual applications."""

import unittest
from unittest.mock import Mock, call, patch

from gi.repository import GLib

from voice_input.desktop import DesktopNotReady, GnomeDesktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.visible = False
        self.bus = Mock()
        self.settings = Mock()
        self.settings.get_strv.side_effect = lambda name: [
            "<Shift><Super>Tab" if name.endswith("backward") else "<Super>Tab"]
        self.bus.call_sync.side_effect = self.reply
        self.keyboard = Mock()
        self.patches = [patch("voice_input.desktop.Gio.bus_get_sync", return_value=self.bus),
                        patch("voice_input.desktop.Gio.Settings.new", return_value=self.settings),
                        patch("voice_input.desktop.time.monotonic", return_value=100)]
        for p in self.patches:
            p.start()
        self.desktop = GnomeDesktop(self.keyboard)

    def tearDown(self):
        self.desktop.close()
        for p in reversed(self.patches):
            p.stop()

    def reply(self, _dest, _path, _interface, method, args, *_rest):
        if method == "Get":
            return GLib.Variant("(v)", (GLib.Variant("b", self.visible),))
        if method == "Set":
            self.visible = args.unpack()[2]
        return GLib.Variant("()", ())

    def test_home_toggles_real_overview_property_without_keyboard_input(self):
        self.assertEqual(self.desktop.toggle_overview()["action"], "overview")
        self.assertTrue(self.visible)
        self.desktop.toggle_overview()
        self.assertFalse(self.visible)
        self.keyboard.assert_not_called()
        self.keyboard.shortcut.assert_not_called()

    def test_overview_focuses_windows_once_then_uses_plain_arrows(self):
        self.visible = True
        self.desktop.move("right")
        self.desktop.move("left")
        self.assertEqual(self.keyboard.shortcut.call_args_list,
                         [call(0xFF09), call(0xFF53), call(0xFF51)])
        self.keyboard.enter.assert_not_called()

    def test_application_switch_uses_current_system_accelerators(self):
        self.desktop.move("right")
        self.desktop.move("left")
        self.assertEqual(self.keyboard.shortcut.call_args_list,
                         [call(0xFF09, [0xFFEB]), call(0xFF09, [0xFFEB, 0xFFE1])])
        self.settings.get_strv.return_value = ["<Control><Alt>Tab"]
        self.settings.get_strv.side_effect = None
        self.desktop.move("right")
        self.keyboard.shortcut.assert_called_with(0xFF09, [0xFFE3, 0xFFE9])

    def test_unset_binding_never_sends_a_plain_tab(self):
        self.settings.get_strv.side_effect = None
        self.settings.get_strv.return_value = []
        with self.assertRaises(RuntimeError):
            self.desktop.move("right")
        self.keyboard.shortcut.assert_not_called()

    def test_home_animation_never_queues_keys_or_confirm(self):
        self.desktop.toggle_overview()
        with self.assertRaises(DesktopNotReady):
            self.desktop.move("right")
        with self.assertRaises(DesktopNotReady):
            self.desktop.confirm_overview()
        self.keyboard.shortcut.assert_not_called()
        self.keyboard.enter.assert_not_called()

    def test_confirm_enters_selected_window_and_blocks_rapid_chat_enter(self):
        self.visible = True
        self.assertTrue(self.desktop.confirm_overview())
        self.keyboard.shortcut.assert_called_once_with(0xFF09)
        self.keyboard.enter.assert_called_once_with()
        self.visible = False
        with self.assertRaises(DesktopNotReady):
            self.desktop.confirm_overview()
        self.keyboard.enter.assert_called_once_with()

    def test_mouse_close_resets_focus_and_next_direction_switches_apps(self):
        self.visible = True
        self.desktop.move("right")
        self.visible = False
        self.desktop.move("left")
        self.keyboard.shortcut.assert_called_with(0xFF09, [0xFFEB, 0xFFE1])
        self.assertFalse(self.desktop.selection_started)

    def test_overview_closing_during_focus_never_leaks_arrow_or_enter(self):
        self.visible = True
        self.keyboard.shortcut.side_effect = lambda *_: setattr(self, "visible", False)
        with self.assertRaises(DesktopNotReady):
            self.desktop.move("right")
        self.assertEqual(self.keyboard.shortcut.call_args_list, [call(0xFF09)])
        self.keyboard.enter.assert_not_called()

    def test_unavailable_shell_never_falls_back_to_app_enter(self):
        self.bus.call_sync.side_effect = GLib.Error("Shell unavailable")
        with self.assertRaises(GLib.Error):
            self.desktop.confirm_overview()
        self.keyboard.enter.assert_not_called()

    def test_overview_property_change_resets_selection(self):
        self.desktop.selection_started = True
        parameters = GLib.Variant("(sa{sv}as)", ("org.gnome.Shell",
                    {"OverviewActive":GLib.Variant("b", False)}, []))
        self.desktop._changed(None, None, None, None, None, parameters, None)
        self.assertFalse(self.desktop.selection_started)
