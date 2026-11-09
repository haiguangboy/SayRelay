"""Main-loop BlueZ GATT adapter. No raw HID access or root permissions.

All asynchronous replies are scoped to a connection generation. Audio stays
in memory and is discarded on disconnect; callbacks run on the GLib thread.
"""

import time
from collections import deque

from gi.repository import Gio, GLib

from .atvv import AudioStream, capabilities, SERVICE_UUID, TX_UUID, RX_UUID, CONTROL_UUID, GET_CAPS

DEVICE = "org.bluez.Device1"
CHAR = "org.bluez.GattCharacteristic1"
SERVICE = "org.bluez.GattService1"
BATTERY = "org.bluez.Battery1"


class RemoteMicrophone:
    def __init__(self, requested, ended, failed):
        self.requested, self.ended, self.failed = requested, ended, failed
        self.bus = None
        self.enabled = False
        self.generation = 0
        self.subscriptions = []
        self.timer = None
        self.device = None
        self.paths = {}
        self.notifying = set()
        self.stream = None
        self.stream_id = 0
        self.release_received = False
        self.started = None
        self.state = "off"
        self.message = "未启用遥控器麦克风"
        self.battery = None
        self.caps = None
        self.deadline = 0
        self.next_retry = 0
        self.retry_delay = 2
        self.caps_attempts = 0
        self.last_audio = 0
        self.last_keepalive = 0
        self.last_capture = None
        self.button_control = False
        self.control_events = deque(maxlen=12)
        self.pending_start = None

    @property
    def active(self):
        return self.state in {"opening", "streaming"}

    def status(self):
        return {"state": self.state, "ready": self.state == "ready", "message": self.message,
                "battery": self.battery, "capabilities": self.caps,
                "audio_packets": self.stream.packets if self.stream else 0,
                "control_events": list(self.control_events),
                "last_capture": self.last_capture}

    def _call(self, path, interface, method, args=None, completed=None, timeout=5000):
        generation = self.generation

        def replied(connection, result, _data):
            try:
                value, error = connection.call_finish(result).unpack(), None
            except GLib.Error as caught:
                value, error = None, caught
            if generation != self.generation or not self.enabled:
                return
            if completed:
                completed(value, error)
            elif error:
                self._fail("遥控器蓝牙通信失败，请检查连接")

        self.bus.call("org.bluez", path, interface, method, args, None,
                      Gio.DBusCallFlags.NONE, timeout, None, replied, None)

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self.state, self.message = "connecting", "正在查找已配对的小米遥控器…"
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            for interface, signal in [("org.freedesktop.DBus.Properties", "PropertiesChanged"),
                                      ("org.freedesktop.DBus.ObjectManager", "InterfacesRemoved")]:
                self.subscriptions.append(self.bus.signal_subscribe(
                    "org.bluez", interface, signal, None, None, Gio.DBusSignalFlags.NONE,
                    self._signal, None))
            self.subscriptions.append(self.bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", "org.bluez", Gio.DBusSignalFlags.NONE,
                self._bluez_changed, None))
            self.timer = GLib.timeout_add(500, self._tick)
            self._discover()
        except GLib.Error:
            self.close()
            self.state, self.message = "error", "无法访问系统蓝牙服务，请先开启蓝牙"

    def _discover(self):
        self.state, self.message = "connecting", "正在连接已配对的小米遥控器…"
        self.deadline = time.monotonic() + 20
        self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                   completed=self._objects)

    def _objects(self, result, error):
        if self.state != "connecting":
            return
        if error:
            self._fail("无法读取蓝牙设备，请检查系统蓝牙服务")
            return
        objects = result[0]
        candidates = [(path, interfaces[DEVICE]) for path, interfaces in objects.items()
                      if DEVICE in interfaces and interfaces[DEVICE].get("Paired")
                      and interfaces[DEVICE].get("Modalias", "").lower().startswith("bluetooth:v2717p32b8")]
        if len(candidates) != 1:
            self._fail("请先在 Ubuntu 蓝牙设置中配对小米遥控器 2 Pro" if not candidates else
                       "检测到多个已配对的小米遥控器，请只保留一个配对设备")
            return
        self.device, props = candidates[0]
        self.battery = objects[self.device].get(BATTERY, {}).get("Percentage")
        if props.get("Blocked"):
            self._fail("遥控器已被系统阻止，请在蓝牙设置中解除阻止")
            return
        if not props.get("Connected"):
            self._call(self.device, DEVICE, "Connect", completed=self._connected, timeout=15000)
        elif props.get("ServicesResolved"):
            self._prepare(objects)
        # Otherwise PropertiesChanged will resume discovery after service resolution.

    def _connected(self, _result, error):
        if error:
            self._fail("遥控器连接失败；请按一下语音键唤醒后重试")
        elif self.state == "connecting":
            self._discover()

    def _prepare(self, objects):
        services = {path for path, interfaces in objects.items()
                    if interfaces.get(SERVICE, {}).get("Device") == self.device
                    and interfaces[SERVICE].get("UUID", "").lower() == SERVICE_UUID}
        chars = {props.get("UUID", "").lower(): (path, props)
                 for path, interfaces in objects.items() if (props := interfaces.get(CHAR))
                 and props.get("Service") in services}
        if any(uuid not in chars for uuid in (TX_UUID, RX_UUID, CONTROL_UUID)):
            self._fail("遥控器未提供完整 ATVV 语音服务")
            return
        if not {"write", "write-without-response"}.intersection(chars[TX_UUID][1].get("Flags", [])):
            self._fail("遥控器语音控制通道不可写")
            return
        self.write_type = "command" if "write-without-response" in chars[TX_UUID][1].get("Flags", []) else "request"
        self.paths = {uuid: chars[uuid][0] for uuid in (TX_UUID, RX_UUID, CONTROL_UUID)}
        self.state, self.message = "subscribing", "正在接通遥控器麦克风…"
        self.deadline = time.monotonic() + 12
        for uuid in (CONTROL_UUID, RX_UUID):
            if "notify" not in chars[uuid][1].get("Flags", []):
                self._fail("遥控器语音通知不可用")
                return

            def subscribed(_result, error, uuid=uuid):
                if error:
                    self._fail("无法订阅遥控器语音，请关闭其他遥控器语音程序后重试")
                    return
                self.notifying.add(uuid)
                if len(self.notifying) == 2:
                    self.state, self.message = "negotiating", "正在协商遥控器音频格式…"
                    self.caps_attempts = 0
                    self._get_caps()

            self._call(self.paths[uuid], CHAR, "StartNotify", completed=subscribed)

    def _get_caps(self):
        self.caps_attempts += 1
        self.deadline = time.monotonic() + 3
        self._write(GET_CAPS)

    def _write(self, data):
        path = self.paths.get(TX_UUID)
        if path:
            self._call(path, CHAR, "WriteValue", GLib.Variant("(aya{sv})", (
                list(data), {"type": GLib.Variant("s", self.write_type)})))

    def _bluez_changed(self, _bus, _sender, _path, _interface, _signal, parameters, _data):
        _name, old, new = parameters.unpack()
        if old != new:
            self._fail("系统蓝牙服务已重启，正在重新连接遥控器")

    def _signal(self, _bus, _sender, path, _interface, signal, parameters, _data):
        if not self.enabled:
            return
        if signal == "InterfacesRemoved":
            removed_path, interfaces = parameters.unpack()
            if ((removed_path == self.device and DEVICE in interfaces) or
                    (removed_path in self.paths.values() and CHAR in interfaces)):
                self._fail("遥控器连接已断开，本次未完成的录音已丢弃")
            return
        interface, changed, invalidated = parameters.unpack()
        if path == self.device and interface == DEVICE:
            if changed.get("Connected") is False or changed.get("ServicesResolved") is False:
                self._fail("遥控器连接已断开，本次未完成的录音已丢弃")
            elif changed.get("ServicesResolved") and self.state == "connecting":
                self._discover()
        elif path == self.device and interface == BATTERY:
            self.battery = changed.get("Percentage", self.battery)
        elif path in self.paths.values() and interface == CHAR:
            if changed.get("Notifying") is False:
                self._fail("遥控器音频通知已停止，本次录音已丢弃")
                return
            if "Value" not in changed:
                return
            try:
                data = bytes(changed["Value"])
                if path == self.paths.get(CONTROL_UUID):
                    self._control(data)
                elif path == self.paths.get(RX_UUID) and self.active:
                    self.stream.append(data)
                    self.last_audio = time.monotonic()
                    if self.stream.decoder is not None:
                        self.state = "streaming"
                elif path == self.paths.get(RX_UUID) and self.pending_start:
                    # GATT can precede evdev by a main-loop turn. Keep only a
                    # short prefix until the actual physical key confirms it.
                    if (time.monotonic() - self.pending_start["at"] <= 0.25 and
                            len(self.pending_start["frames"]) < 32):
                        self.pending_start["frames"].append(data)
            except (ValueError, IndexError) as error:
                self._fail(str(error))

    def _control(self, data):
        if not data:
            return
        self.control_events.append(data[:16].hex(" "))
        opcode = data[0]
        if opcode == 0x0B and self.state == "negotiating":
            self.caps = capabilities(data)
            self.state, self.message = "ready", "遥控器已就绪：按住语音键说话，松开后转写"
            self.retry_delay = 2
        elif opcode == 0x08:
            if self.button_control:
                # Linux HID owns push-to-talk; do not double-toggle if firmware
                # also emits START_SEARCH for the same physical press.
                return
            if self.active:
                self.ended()
            elif self.state == "ready":
                self.requested()
        elif opcode == 0x04 and self.state in {"ready", "opening", "streaming"}:
            if len(data) < 4 or data[2] != 2:
                raise ValueError("遥控器没有开始 16 kHz 录音")
            if self.state == "ready":
                if self.button_control:
                    self.pending_start = {"data": data, "at": time.monotonic(), "frames": []}
                    return
                # Firmware can begin audio directly on physical voice press,
                # without START_SEARCH or a host MIC_OPEN.
                self.requested()
            if not self.active:
                self._write(bytes((0x0D, data[3])))
                return
            if self.state == "streaming" and self.stream_id != data[3]:
                # An actual new stream has new ADPCM state; never carry the
                # previous predictor/index into it.
                self.stream = AudioStream()
            self.stream_id = data[3]
            self.stream.begin()
            self.state = "streaming"
        elif opcode == 0x0A and self.active:
            self.stream.sync(data)
        elif opcode == 0x00 and self.active:
            self.release_received = True
            self.ended()
        elif opcode == 0x00:
            self.pending_start = None
        elif opcode == 0x0C and self.active:
            self._fail("遥控器拒绝开启麦克风，请松开语音键后重试")

    def start(self, _source=""):
        if self.state != "ready":
            raise RuntimeError(self.message)
        self.stream = AudioStream()
        self.stream_id, self.release_received = 0, False
        self.started = self.last_audio = self.last_keepalive = time.monotonic()
        self.state, self.message = "opening", "正在使用遥控器录音，松开语音键后转写"
        self.deadline = self.started + 5
        if self.button_control:
            pending, self.pending_start = self.pending_start, None
            if pending and time.monotonic() - pending["at"] <= 0.25:
                # A physical key press has now confirmed the early start.
                data = pending["data"]
                self.stream_id = data[3]
                self.stream.begin()
                self.state = "streaming"
                for frame in pending["frames"]:
                    self.stream.append(frame)
                return
            stream, generation = self.stream, self.generation

            def open_if_waiting():
                if (self.generation == generation and self.stream is stream and self.state == "opening"):
                    self._write(b"\x0c\x00")
                return GLib.SOURCE_REMOVE

            # Allow the remote's automatic AUDIO_START to arrive first. A
            # second MIC_OPEN would reset the encoder mid-utterance.
            GLib.timeout_add(180, open_if_waiting)
        else:
            self._write(b"\x0c\x00")

    def stop(self):
        if not self.active:
            raise RuntimeError("当前没有遥控器录音")
        stream = self.stream
        self.last_capture = {"audio_packets": stream.packets,
                             "unsynced_packets": stream.unsynced_packets,
                             "start_received": stream.start_received,
                             "sync_count": stream.sync_count,
                             "audio_seconds": len(stream.samples) / 16000}
        self.discard()
        return stream.pcm()

    def discard(self):
        if self.active:
            if not self.release_received:
                self._write(bytes((0x0D, self.stream_id)))
            self.state, self.message = "ready", "遥控器已就绪：按住语音键说话，松开后转写"
        self.stream = None
        self.pending_start = None

    def check(self):
        if not self.active:
            raise RuntimeError("遥控器录音已中断")

    def _tick(self):
        if not self.enabled:
            return GLib.SOURCE_REMOVE
        now = time.monotonic()
        if self.state == "error" and now >= self.next_retry:
            self._discover()
        elif self.state == "negotiating" and now >= self.deadline:
            if self.caps_attempts < 3:
                self._get_caps()
            else:
                self._fail("遥控器未响应语音协商；请按语音键唤醒，正在重试")
        elif self.state in {"connecting", "subscribing", "opening"} and now >= self.deadline:
            self._fail("遥控器语音连接超时，请松开语音键后重试")
        elif self.state == "streaming":
            if now - self.last_audio > 5:
                self._fail("遥控器音频中断，本次未完成的录音已丢弃")
            elif now - self.last_keepalive >= 10:
                self.last_keepalive = now
                self._write(bytes((0x0E, self.stream_id)))
        return GLib.SOURCE_CONTINUE

    def _reset(self):
        self.discard()
        self.generation += 1
        if self.bus:
            for uuid in (RX_UUID, CONTROL_UUID):
                if path := self.paths.get(uuid):
                    # Sent after any pending StartNotify, before a future session.
                    self.bus.call("org.bluez", path, CHAR, "StopNotify", None, None,
                                  Gio.DBusCallFlags.NONE, 2000, None, None, None)
        self.paths = {}
        self.notifying.clear()
        self.device = None
        self.caps = None

    def _fail(self, message):
        was_active = self.active
        self._reset()
        self.state, self.message = "error", message
        self.next_retry = time.monotonic() + self.retry_delay
        self.retry_delay = min(30, self.retry_delay * 2)
        if was_active:
            self.failed(message)

    def close(self):
        self._reset()
        self.enabled = False
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.bus:
            for subscription in self.subscriptions:
                self.bus.signal_unsubscribe(subscription)
        self.subscriptions.clear()
        self.battery = None
        self.state, self.message = "off", "未启用遥控器麦克风"
