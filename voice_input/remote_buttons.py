"""Linux evdev remote buttons, exclusively scoped to Xiaomi RC003 hardware.

The optional udev rule grants the active desktop user access to this remote.
EVIOCGRAB prevents its native F5 repeat and other buttons reaching applications.
The computer's keyboard is never opened or grabbed.
"""

import errno
import fcntl
import os
import struct
from pathlib import Path

from gi.repository import GLib

EVENT = struct.Struct("@llHHi")
EVIOCGID = 0x80084502
EVIOCGRAB = 0x40044590
VOICE_KEY = 63  # Linux KEY_F5, from HID Keyboard usage 0x3e.
CONFIRM_KEY = 28  # Linux KEY_ENTER, from HID Keyboard usage 0x28.
REMOTE_ID = (5, 0x2717, 0x32B8)


def remote_nodes():
    for event in sorted(Path("/sys/class/input").glob("event*")):
        try:
            ids = tuple(int((event / "device/id" / key).read_text().strip(), 16)
                        for key in ("bustype", "vendor", "product"))
        except (OSError, ValueError):
            continue
        if ids == REMOTE_ID:
            yield Path("/dev/input") / event.name


class RemoteButtons:
    def __init__(self, pressed, released, failed, confirmed=None):
        self.pressed, self.released, self.failed = pressed, released, failed
        self.confirmed = confirmed
        self.fd = None
        self.watch = None
        self.timer = None
        self.enabled = False
        self.held = False
        self.confirm_held = False
        self.state = "off"
        self.message = "未启用遥控器按键"
        self.presses = 0
        self.confirm_presses = 0

    @property
    def ready(self):
        return self.fd is not None

    def status(self):
        return {"state": self.state, "ready": self.ready, "message": self.message,
                "voice_presses": self.presses, "confirm_presses": self.confirm_presses}

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self._connect()
        self.timer = GLib.timeout_add(1500, self._poll)

    def _poll(self):
        if not self.enabled:
            return GLib.SOURCE_REMOVE
        if self.fd is None:
            self._connect()
        return GLib.SOURCE_CONTINUE

    def _connect(self):
        nodes = list(remote_nodes())
        if len(nodes) != 1:
            self.state, self.message = "waiting", "等待唯一的小米遥控器按键设备连接"
            return
        fd = None
        try:
            fd = os.open(nodes[0], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            identity = bytearray(8)
            fcntl.ioctl(fd, EVIOCGID, identity)
            if struct.unpack("@HHHH", identity)[:3] != REMOTE_ID:
                raise OSError(errno.ENODEV, "遥控器身份发生变化")
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            self.fd = fd
            fd = None
            self.held = False
            self.confirm_held = False
            self.watch = GLib.io_add_watch(self.fd, GLib.PRIORITY_DEFAULT,
                                          GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, self._read)
            self.state, self.message = "ready", "遥控器按键已接通（语音键录音，确定键 Enter）"
        except PermissionError:
            self.state, self.message = "permission", "请点“启用遥控器按键”，完成一次系统设备授权"
        except OSError as error:
            self.state = "error"
            self.message = "遥控器正被其他程序占用" if error.errno == errno.EBUSY else "遥控器按键暂不可用，正在重试"
        finally:
            if fd is not None:
                os.close(fd)

    def _event(self, kind, code, value):
        if kind == 0 and code == 3:  # SYN_DROPPED: lost release is unsafe.
            raise OSError("遥控器按键事件丢失，本次录音已取消")
        if kind != 1:
            return
        if code == CONFIRM_KEY:
            if value == 1 and not self.confirm_held:
                self.confirm_held = True
                self.confirm_presses += 1
                if self.confirmed:
                    self.confirmed()
            elif value == 0:
                self.confirm_held = False
            return
        if code != VOICE_KEY:
            return
        if value == 1 and not self.held:
            self.held = True
            self.presses += 1
            self.pressed()
        elif value == 0 and self.held:
            self.held = False
            self.released()
        # value=2 is kernel autorepeat and must not toggle recording.

    def _read(self, _fd, condition):
        try:
            if condition & (GLib.IO_ERR | GLib.IO_HUP):
                raise OSError("遥控器按键连接已断开，本次录音已取消")
            raw = os.read(self.fd, EVENT.size * 64)
            if not raw or len(raw) % EVENT.size:
                raise OSError("遥控器按键数据中断，本次录音已取消")
            for _seconds, _micros, kind, code, value in EVENT.iter_unpack(raw):
                self._event(kind, code, value)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError as error:
            self.watch = None
            self._disconnect()
            self.state, self.message = "waiting", str(error)
            self.failed(str(error))
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _disconnect(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.fd is not None:
            # Closing the descriptor releases EVIOCGRAB, including on crash.
            os.close(self.fd)
            self.fd = None
        self.held = False
        self.confirm_held = False

    def close(self):
        self.enabled = False
        self._disconnect()
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.state, self.message = "off", "未启用遥控器按键"
