"""ATVV wire format, BlueZ lifecycle and remote-to-recognizer boundaries."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from gi.repository import GLib

from voice_input.atvv import ADPCMDecoder, AudioStream, capabilities, SERVICE_UUID, TX_UUID, RX_UUID, CONTROL_UUID
from voice_input.config import Config
from voice_input.remote import RemoteMicrophone, DEVICE, SERVICE, CHAR, BATTERY
from voice_input.remote_buttons import RemoteButtons, EVIOCGID, EVIOCGRAB, VOICE_KEY
from voice_input.service import Controller
from test_pipeline import FakeRecognizer, Keyboard, spin


CAPS = bytes.fromhex("0b 01 00 02 03 00 78 00 00")
SYNC = bytes.fromhex("0a 02 00 00 00 00 00")


class CodecTests(unittest.TestCase):
    def test_high_nibble_first_signed_output_and_packet_continuity(self):
        # Hand-calculated from predictor=0, index=0: asymmetric nibbles expose
        # reversed byte order. Splitting BLE notifications must retain state.
        decoder = ADPCMDecoder(0, 0)
        observed = list(decoder.decode(b"\x17\x80")) + list(decoder.decode(b"\xf2"))
        self.assertEqual(observed, [1, 12, 10, 11, -12, 5])
        self.assertEqual(list(ADPCMDecoder(32760, 88).decode(b"\x77\xff")),
                         [32767, 32767, -28669, -32768])

    def test_standard_and_firmware_2671_capabilities(self):
        self.assertEqual(capabilities(CAPS)["sample_rate"], 16000)
        self.assertEqual(capabilities(bytes.fromhex("0b 01 00 00 03 00 78 00 00"))["frame_bytes"], 120)
        for invalid in [b"", bytes.fromhex("0b 00 04 00 02 00 78 00 00"),
                        bytes.fromhex("0b 01 00 01 00 00 78"),
                        bytes.fromhex("0b 01 00 02 03 00 00")]:
            with self.subTest(data=invalid), self.assertRaises(ValueError):
                capabilities(invalid)

    def test_missing_sync_is_never_transcribed_as_valid_audio(self):
        stream = AudioStream()
        stream.append(b"\x17")
        stream.sync(SYNC)
        stream.append(b"\x17")
        with self.assertRaisesRegex(ValueError, "同步"):
            stream.pcm()

    def test_new_recording_does_not_reuse_previous_predictor(self):
        first, second = AudioStream(), AudioStream()
        first.sync(SYNC)
        first.append(b"\x77" * 20)
        second.append(b"\x17")
        self.assertEqual(len(second.samples), 0)
        second = AudioStream()
        second.sync(SYNC)
        second.append(b"\x17")
        np.testing.assert_array_equal(second.pcm(), np.array([1, 12], dtype=np.float32) / 32768)

    def test_audio_memory_and_sync_parameters_are_bounded(self):
        stream = AudioStream(max_seconds=1)
        stream.sync(SYNC)
        for _ in range(16):
            stream.append(bytes(500))
        with self.assertRaises(ValueError):
            stream.append(b"\x00")
        for invalid in [b"\x0a", SYNC[:-1] + b"\x59", b"\x0a\x01" + SYNC[2:]]:
            with self.subTest(data=invalid), self.assertRaises(ValueError):
                AudioStream().sync(invalid)

    def test_firmware_without_sync_starts_at_zero_only_after_audio_start(self):
        stream = AudioStream()
        stream.begin()
        stream.append(b"\x17\x80")
        stream.append(b"\xf2")
        np.testing.assert_array_equal(stream.pcm(),
                                      np.array([1, 12, 10, 11, -12, 5], dtype=np.float32) / 32768)
        self.assertEqual(stream.sync_count, 0)

    def test_explicit_sync_overrides_initial_state_without_changing_prior_audio(self):
        stream = AudioStream()
        stream.begin()
        stream.append(b"\x77")
        stream.sync(bytes.fromhex("0a 02 00 01 00 00 00"))
        stream.append(b"\x17")
        np.testing.assert_array_equal(stream.pcm(), np.array([11, 41, 1, 12], dtype=np.float32) / 32768)

    def test_duplicate_audio_start_cannot_reset_an_active_decoder(self):
        stream = AudioStream()
        stream.begin()
        stream.append(b"\x17\x80")
        stream.begin()
        stream.append(b"\xf2")
        np.testing.assert_array_equal(stream.pcm(),
                                      np.array([1, 12, 10, 11, -12, 5], dtype=np.float32) / 32768)


class Bus:
    def __init__(self):
        self.calls = []
        self.subscriptions = set()
        self.next_id = 1

    def call(self, dest, path, interface, method, args, reply_type, flags, timeout, cancellable, callback, data):
        self.calls.append(SimpleNamespace(path=path, method=method, args=args,
                                          callback=callback, replied=False))

    def call_finish(self, result):
        return SimpleNamespace(unpack=lambda: result)

    def reply(self, method, value=()):
        call = next(call for call in self.calls if call.method == method and not call.replied)
        call.replied = True
        if call.callback:
            call.callback(self, value, None)

    def signal_subscribe(self, *args):
        sub_id = self.next_id
        self.next_id += 1
        self.subscriptions.add(sub_id)
        return sub_id

    def signal_unsubscribe(self, sub_id):
        self.subscriptions.remove(sub_id)


def objects():
    # Deliberately no Name/Alias or hardware address: discovery must use IDs
    # and characteristic relationships, not names or cached attribute handles.
    device, service = "/org/bluez/hci0/test_remote", "/org/bluez/hci0/test_remote/service1234"
    result = {device: {DEVICE: {"Paired": True, "Connected": True, "ServicesResolved": True,
                                "Modalias": "bluetooth:v2717p32B8d00A4"}, BATTERY: {"Percentage": 92}},
              service: {SERVICE: {"Device": device, "UUID": SERVICE_UUID}}}
    for suffix, uuid, flags in [("a", TX_UUID, ["write-without-response"]),
                                 ("b", RX_UUID, ["notify"]), ("c", CONTROL_UUID, ["notify"])]:
        result[service + "/char_" + suffix] = {CHAR: {"UUID": uuid, "Service": service, "Flags": flags}}
    return result


class BlueZTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        self.errors, self.requests, self.ended = [], [], []
        self.remote = RemoteMicrophone(lambda: self.requests.append(True),
                                       lambda: self.ended.append(True), self.errors.append)
        with patch("voice_input.remote.Gio.bus_get_sync", return_value=self.bus):
            self.remote.enable()

    def tearDown(self):
        self.remote.close()

    def ready(self):
        self.bus.reply("GetManagedObjects", (objects(),))
        self.bus.reply("StartNotify")
        self.bus.reply("StartNotify")
        self.remote._control(CAPS)

    def test_dynamic_discovery_subscribes_both_channels_before_caps(self):
        self.bus.reply("GetManagedObjects", (objects(),))
        self.assertFalse(any(call.method == "WriteValue" for call in self.bus.calls))
        self.bus.reply("StartNotify")
        self.assertFalse(any(call.method == "WriteValue" for call in self.bus.calls))
        self.bus.reply("StartNotify")
        self.remote._control(CAPS)
        self.assertEqual(self.remote.state, "ready")
        self.assertEqual(self.remote.battery, 92)

    def test_late_subscribe_reply_after_close_never_restarts_remote(self):
        self.bus.reply("GetManagedObjects", (objects(),))
        self.remote.close()
        self.bus.reply("StartNotify")
        self.bus.reply("StartNotify")
        self.assertEqual(self.remote.state, "off")
        self.assertFalse(any(call.method == "WriteValue" for call in self.bus.calls))
        self.assertEqual(self.bus.subscriptions, set())
        self.assertEqual(sum(call.method == "StopNotify" for call in self.bus.calls), 2)

    def test_duplicate_discovery_reply_does_not_subscribe_twice(self):
        self.ready()
        self.remote._objects((objects(),), None)
        self.assertEqual(sum(call.method == "StartNotify" for call in self.bus.calls), 2)

    def test_disconnect_discards_audio_and_stale_audio_start_is_ignored(self):
        self.ready()
        self.remote.start()
        self.remote._control(SYNC)
        self.remote.stream.append(b"\x17")
        device = self.remote.device
        self.remote._signal(None, None, device, None, "PropertiesChanged",
                            GLib.Variant("(sa{sv}as)", (DEVICE, {"Connected": GLib.Variant("b", False)}, [])), None)
        self.assertEqual(len(self.errors), 1)
        self.assertIsNone(self.remote.stream)
        self.remote._control(b"\x04\x00\x02\x01")
        self.assertEqual(self.remote.state, "error")
        self.assertEqual(self.ended, [])

    def test_release_freezes_pcm_without_opening_another_recording(self):
        self.ready()
        self.remote._control(b"\x08")
        self.assertEqual(self.requests, [True])
        self.remote.start()
        self.remote._control(b"\x04\x00\x02\x01")
        self.remote._control(SYNC)
        self.remote.stream.append(b"\x17")
        self.remote._control(b"\x00\x00")
        self.assertEqual(self.ended, [True])
        np.testing.assert_array_equal(self.remote.stop(), np.array([1, 12], dtype=np.float32) / 32768)
        self.remote._control(b"\x00\x00")
        self.assertEqual(self.ended, [True])
        self.assertEqual(self.remote.state, "ready")

    def test_audio_start_without_sync_decodes_firmware_2671_stream(self):
        self.ready()
        self.remote.start()
        self.remote._control(b"\x04\x00\x02\x01")
        self.remote.stream.append(b"\x17")
        np.testing.assert_array_equal(self.remote.stop(), np.array([1, 12], dtype=np.float32) / 32768)
        self.assertEqual(self.remote.last_capture["sync_count"], 0)

    def test_new_stream_id_resets_predictor_and_discards_previous_stream(self):
        self.ready()
        self.remote.start()
        self.remote._control(b"\x04\x03\x02\x09")
        self.remote.stream.append(b"\x77" * 30)
        self.remote._control(b"\x04\x00\x02\x00")
        self.remote.stream.append(b"\x17")
        np.testing.assert_array_equal(self.remote.stop(), np.array([1, 12], dtype=np.float32) / 32768)

    def test_automatic_audio_start_avoids_duplicate_host_mic_open(self):
        self.ready()
        self.remote.button_control = True
        callbacks = []
        with patch("voice_input.remote.GLib.timeout_add", side_effect=lambda _delay, callback: callbacks.append(callback)):
            self.remote.start()
        self.remote._control(b"\x04\x03\x02\x09")
        callbacks[0]()
        writes = [call.args.unpack()[0] for call in self.bus.calls if call.method == "WriteValue"]
        self.assertNotIn([0x0C, 0x00], writes)

    def test_manual_start_falls_back_to_host_open_if_remote_does_not_start(self):
        self.ready()
        self.remote.button_control = True
        callbacks = []
        with patch("voice_input.remote.GLib.timeout_add", side_effect=lambda _delay, callback: callbacks.append(callback)):
            self.remote.start()
        callbacks[0]()
        writes = [call.args.unpack()[0] for call in self.bus.calls if call.method == "WriteValue"]
        self.assertIn([0x0C, 0x00], writes)


class RemotePipelineTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch("voice_input.service.Config.load", return_value=Config()),
                        patch("voice_input.service.Recognizer", FakeRecognizer),
                        patch("voice_input.service.PortalKeyboard", Keyboard),
                        patch("voice_input.service.notify"), patch("voice_input.service.copy_text")]
        self.mocks = [p.start() for p in self.patches]
        FakeRecognizer.gate = None
        self.controller = Controller()
        spin(lambda: self.controller.state == "idle")
        self.controller.config.input_mode = "remote"
        self.controller.config.output = "clipboard"
        self.controller.remote.state = "ready"

    def tearDown(self):
        self.controller.close()
        for p in reversed(self.patches):
            p.stop()

    def speak(self):
        self.controller.remote_buttons._event(1, VOICE_KEY, 1)
        self.controller.remote._control(b"\x04\x00\x02\x01")
        self.controller.remote._control(SYNC)
        self.controller.remote.stream.append(bytes(120))

    def test_remote_release_reuses_recognizer_and_output_delivery(self):
        self.speak()
        self.assertIs(self.controller.capture, self.controller.remote)
        self.assertGreaterEqual(self.controller.status()["recording_seconds"], 0)
        self.controller.remote._control(b"\x00")
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["input_mode"], "remote")
        self.mocks[-1].assert_called_once_with("公开测试文字")

    def test_cancel_ignores_late_release_and_never_transcribes(self):
        self.speak()
        self.controller.cancel()
        self.controller.remote._control(b"\x00")
        self.assertEqual(self.controller.job["state"], "cancelled")
        self.mocks[-1].assert_not_called()

    def test_disconnect_ends_job_without_transcribing_partial_audio(self):
        self.speak()
        self.controller.remote._fail("断开")
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.controller.job["state"], "error")
        self.assertIsNone(self.controller.remote.stream)
        self.mocks[-1].assert_not_called()

    def test_local_mode_ignores_remote_voice_button(self):
        self.controller.config.input_mode = "local"
        self.controller.remote_buttons._event(1, VOICE_KEY, 1)
        self.assertIsNone(self.controller.job)
        self.assertEqual(self.controller.state, "idle")

    def test_remote_preflight_never_opens_mic_without_paste_permission(self):
        self.controller.config.output = "paste"
        self.controller.remote_buttons._event(1, VOICE_KEY, 1)
        self.assertIsNone(self.controller.job)
        self.assertEqual(self.controller.remote.state, "ready")
        self.assertEqual(self.controller.keyboard.connections, 1)

    def test_switching_sources_is_saved_and_keeps_local_device_choice(self):
        self.controller.config.source = "my-usb-microphone"
        with patch.object(Config, "save"), patch("voice_input.service.Path.is_file", return_value=True):
            self.controller.dispatch({"action": "configure", "values": {"input_mode": "local"}})
        self.assertEqual(self.controller.remote.state, "off")
        self.assertEqual(self.controller.config.source, "my-usb-microphone")
        self.assertEqual(Config().input_mode, "local")
        with self.assertRaises(ValueError):
            Config(input_mode="unknown").validate()

    def test_both_hid_and_gatt_start_do_not_double_toggle_recording(self):
        self.speak()
        self.controller.remote._control(b"\x08")
        self.assertEqual(self.controller.state, "recording")
        self.controller.remote_buttons._event(1, VOICE_KEY, 0)
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["state"], "done")

    def test_early_gatt_start_waits_for_hid_press_and_preserves_first_audio(self):
        self.controller.remote._control(b"\x04\x03\x02\x09")
        self.assertIsNone(self.controller.job)
        self.controller.remote.pending_start["frames"].append(b"\x17")
        self.controller.remote_buttons._event(1, VOICE_KEY, 1)
        self.assertEqual(self.controller.state, "recording")
        self.assertEqual(self.controller.remote.stream.packets, 1)
        self.controller.remote.stream.append(bytes(120))
        self.controller.remote_buttons._event(1, VOICE_KEY, 0)
        spin(lambda: self.controller.state == "idle")
        self.assertEqual(self.controller.job["state"], "done")

    def test_late_gatt_start_after_cancel_cannot_create_another_job(self):
        self.speak()
        job_id = self.controller.job["id"]
        self.controller.cancel()
        self.controller.remote._control(b"\x04\x03\x02\x09")
        self.controller.remote._control(b"\x00")
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.controller.job["id"], job_id)
        self.assertEqual(self.controller.job["state"], "cancelled")
        self.assertIsNone(self.controller.remote.pending_start)


class RemoteButtonTests(unittest.TestCase):
    def setUp(self):
        self.presses, self.releases, self.errors = [], [], []
        self.buttons = RemoteButtons(lambda: self.presses.append(True),
                                     lambda: self.releases.append(True), self.errors.append)

    def test_voice_key_repeat_is_ignored_and_release_is_paired(self):
        self.buttons._event(1, VOICE_KEY, 0)
        self.buttons._event(1, VOICE_KEY, 1)
        self.buttons._event(1, VOICE_KEY, 1)
        for _ in range(200):
            self.buttons._event(1, VOICE_KEY, 2)
        self.buttons._event(1, 30, 1)
        self.buttons._event(1, VOICE_KEY, 0)
        self.buttons._event(1, VOICE_KEY, 0)
        self.assertEqual(self.presses, [True])
        self.assertEqual(self.releases, [True])

    def test_device_identity_is_rechecked_before_exclusive_grab(self):
        import struct
        calls = []

        def ioctl(fd, command, arg):
            calls.append(command)
            if command == EVIOCGID:
                arg[:] = struct.pack("@HHHH", 3, 0x1234, 0x5678, 1)

        with patch("voice_input.remote_buttons.remote_nodes", return_value=["/dev/input/event999"]), \
                patch("voice_input.remote_buttons.os.open", return_value=42), \
                patch("voice_input.remote_buttons.os.close") as close, \
                patch("voice_input.remote_buttons.fcntl.ioctl", side_effect=ioctl):
            self.buttons._connect()
        self.assertEqual(calls, [EVIOCGID])
        close.assert_called_once_with(42)
        self.assertFalse(self.buttons.ready)

    def test_permission_denied_reports_specific_device_setup(self):
        with patch("voice_input.remote_buttons.remote_nodes", return_value=["/dev/input/event999"]), \
                patch("voice_input.remote_buttons.os.open", side_effect=PermissionError):
            self.buttons._connect()
        self.assertEqual(self.buttons.state, "permission")
        self.assertFalse(self.buttons.ready)

    def test_exact_remote_is_grabbed_and_close_releases_descriptor(self):
        import struct
        calls = []

        def ioctl(fd, command, arg):
            calls.append(command)
            if command == EVIOCGID:
                arg[:] = struct.pack("@HHHH", 5, 0x2717, 0x32B8, 1)

        with patch("voice_input.remote_buttons.remote_nodes", return_value=["/dev/input/event999"]), \
                patch("voice_input.remote_buttons.os.open", return_value=42), \
                patch("voice_input.remote_buttons.os.close") as close, \
                patch("voice_input.remote_buttons.fcntl.ioctl", side_effect=ioctl), \
                patch("voice_input.remote_buttons.GLib.io_add_watch", return_value=1), \
                patch("voice_input.remote_buttons.GLib.source_remove"):
            self.buttons._connect()
            self.assertTrue(self.buttons.ready)
            self.buttons.close()
        self.assertEqual(calls, [EVIOCGID, EVIOCGRAB])
        close.assert_called_once_with(42)
        self.assertFalse(self.buttons.ready)

    def test_disconnect_cancels_instead_of_emitting_normal_release(self):
        self.buttons.fd = 42
        self.buttons.held = True
        with patch("voice_input.remote_buttons.os.close"):
            self.buttons._read(42, GLib.IO_HUP)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.releases, [])
        self.assertFalse(self.buttons.ready)

    def test_event_overflow_cannot_leave_microphone_stuck_on(self):
        from voice_input.remote_buttons import EVENT
        self.buttons.fd = 42
        self.buttons.held = True
        with patch("voice_input.remote_buttons.os.read", return_value=EVENT.pack(0, 0, 0, 3, 0)), \
                patch("voice_input.remote_buttons.os.close"):
            self.buttons._read(42, GLib.IO_IN)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.buttons.held)


if __name__ == "__main__":
    unittest.main()
