"""Portal persistence protocol; fake the OS responses, never open a permission dialog."""

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import GLib

from voice_input.keyboard import PortalKeyboard


class KeyboardPermissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "config"
        self.token_file = self.root / "keyboard-restore-token"
        self.bus = Mock()
        self.bus.call_sync.return_value = GLib.Variant("(v)", (GLib.Variant("u", 2),))
        self.patches = [patch("voice_input.keyboard.CONFIG_DIR", self.root),
                        patch("voice_input.keyboard.AUTH_FILE", self.token_file),
                        patch("voice_input.keyboard.Gio.bus_get_sync", return_value=self.bus)]
        for item in self.patches:
            item.start()
        self.messages = []
        self.keyboard = PortalKeyboard(self.messages.append)

    def tearDown(self):
        self.keyboard.close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def authorize(self, new_token=None):
        calls = []

        def reply(method, signature, args, completed):
            calls.append((method, args))
            if method == "CreateSession":
                completed(None, {"session_handle": "/test/session"})
            elif method == "SelectDevices":
                completed(None, {})
            elif method == "Start":
                completed(None, {"devices": 1, **({"restore_token": new_token} if new_token else {})})

        with patch.object(self.keyboard, "_request", side_effect=reply):
            self.keyboard.connect()
        return calls

    def test_first_grant_requests_persistent_keyboard_only(self):
        token = str(uuid.uuid4())
        calls = self.authorize(token)
        options = calls[1][1][-1]
        self.assertEqual({key: value.unpack() for key, value in options.items()},
                         {"types": 1, "persist_mode": 2})
        self.assertEqual(self.keyboard.session, "/test/session")
        self.assertEqual(self.token_file.read_text().strip(), token)
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_restart_reuses_token_then_saves_replacement(self):
        old_token, new_token = str(uuid.uuid4()), str(uuid.uuid4())
        self.keyboard._store_restore_token(old_token)
        self.keyboard.close()
        self.keyboard = PortalKeyboard(self.messages.append)
        calls = self.authorize(new_token)
        self.assertEqual(calls[1][1][-1]["restore_token"].unpack(), old_token)
        self.assertEqual(self.keyboard.restore_token, new_token)
        self.keyboard.close()
        self.assertEqual(self.token_file.read_text().strip(), new_token)

    def test_consumed_token_is_not_retried_after_failed_restore(self):
        self.keyboard._store_restore_token(str(uuid.uuid4()))

        def reply(method, signature, args, completed):
            if method == "CreateSession":
                completed(None, {"session_handle": "/test/session"})
            elif method == "SelectDevices":
                self.assertFalse(self.token_file.exists())
                completed(RuntimeError("withdrawn"), None)

        with patch.object(self.keyboard, "_request", side_effect=reply):
            self.keyboard.connect()
        self.assertIsNone(self.keyboard.session)
        self.assertFalse(self.keyboard.connecting)
        self.assertEqual(self.keyboard.restore_token, "")

    def test_permission_closed_is_not_immediately_reauthorized(self):
        self.authorize(str(uuid.uuid4()))
        with patch.object(self.keyboard, "connect") as connect:
            self.keyboard._closed()
        connect.assert_not_called()
        self.assertIsNone(self.keyboard.session)
        self.assertTrue(self.keyboard.restore_token)

    def test_portal_v1_uses_temporary_permission(self):
        self.bus.call_sync.return_value = GLib.Variant("(v)", (GLib.Variant("u", 1),))
        calls = self.authorize()
        self.assertEqual(list(calls[1][1][-1]), ["types"])
        self.assertEqual(self.keyboard.restore_token, "")

    def test_failed_keyboard_event_invalidates_stale_session(self):
        self.authorize(str(uuid.uuid4()))
        self.bus.call_sync.side_effect = GLib.Error("session ended")
        with self.assertRaises(GLib.Error):
            self.keyboard.paste()
        self.assertIsNone(self.keyboard.session)
        self.assertTrue(self.keyboard.restore_token)

    def test_enter_is_one_unmodified_return_press_and_release(self):
        self.authorize()
        self.bus.call_sync.reset_mock()
        self.keyboard.enter()
        calls = self.bus.call_sync.call_args_list
        self.assertEqual([call.args[3] for call in calls],
                         ["NotifyKeyboardKeysym", "NotifyKeyboardKeysym"])
        self.assertEqual([call.args[4].unpack() for call in calls],
                         [("/test/session", {}, 0xFF0D, 1), ("/test/session", {}, 0xFF0D, 0)])

    def test_enter_without_permission_never_calls_portal(self):
        self.bus.call_sync.reset_mock()
        with self.assertRaises(RuntimeError):
            self.keyboard.enter()
        self.bus.call_sync.assert_not_called()

    def test_application_shortcut_releases_all_keys_in_reverse_order(self):
        with patch.object(self.keyboard, "key") as key:
            self.keyboard.shortcut(0xFF09, [0xFFEB, 0xFFE1])
        self.assertEqual([call.args for call in key.call_args_list],
                         [(0xFFEB, True), (0xFFE1, True), (0xFF09, True),
                          (0xFF09, False), (0xFFE1, False), (0xFFEB, False)])

    def test_failed_shortcut_press_releases_prior_modifiers(self):
        def press(keysym, pressed):
            if keysym == 0xFF09 and pressed:
                raise RuntimeError("failed")
        with patch.object(self.keyboard, "key", side_effect=press) as key:
            with self.assertRaises(RuntimeError):
                self.keyboard.shortcut(0xFF09, [0xFFEB, 0xFFE1])
        self.assertEqual([call.args for call in key.call_args_list][-2:],
                         [(0xFFE1, False), (0xFFEB, False)])


if __name__ == "__main__":
    unittest.main()
