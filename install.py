"""Install user-scoped launcher, service, and GNOME shortcuts; no root needed."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

ROOT = Path(__file__).resolve().parent
HOME_DIR = Path.home()
APPLICATION = HOME_DIR / ".local/share/applications/voice-input.desktop"
UNIT = HOME_DIR / ".config/systemd/user/voice-input.service"
LAUNCHER = HOME_DIR / ".local/bin/voice-input"
BINDINGS = {
    "toggle": ("语音输入：开始或结束", "<Control><Alt>space"),
    "cancel": ("语音输入：取消", "<Control><Alt><Shift>space"),
}
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = SCHEMA + ".custom-keybinding"


def shortcut_path(action):
    return BASE + "voice_input_" + action + "/"


def install():
    python = ROOT / ".venv/bin/python"
    if not python.exists() or not (ROOT / "models/small/model.bin").exists():
        raise RuntimeError("请先准备虚拟环境和模型，见 README")
    settings = Gio.Settings.new(SCHEMA)
    paths = list(settings.get_strv("custom-keybindings"))
    own_paths = {shortcut_path(action) for action in BINDINGS}
    desired = {Gtk.accelerator_parse(item[1]) for item in BINDINGS.values()}
    schemas, _ = Gio.SettingsSchemaSource.get_default().list_schemas(True)
    for schema in schemas:
        if not schema.startswith("org.gnome.") or not ("keybindings" in schema or schema == SCHEMA):
            continue
        builtins = Gio.Settings.new(schema)
        for key in builtins.props.settings_schema.list_keys():
            value = builtins.get_value(key)
            candidates = value.unpack() if value.get_type_string() == "as" else [value.unpack()] if value.get_type_string() == "s" else []
            for binding in candidates:
                if binding and Gtk.accelerator_parse(binding) in desired:
                    raise RuntimeError(f"快捷键与系统配置冲突：{schema} / {key}")
    for path in paths:
        if path in own_paths:
            continue
        existing = Gio.Settings.new_with_path(CUSTOM, path)
        if Gtk.accelerator_parse(existing.get_string("binding")) in desired:
            raise RuntimeError(f"快捷键与已有配置冲突：{existing.get_string('name')}")
    for target in [LAUNCHER, UNIT, APPLICATION]:
        target.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text("#!/bin/sh\ncd " + shlex.quote(str(ROOT)) + "\nexec " +
                        shlex.quote(str(python)) + " -m voice_input.cli \"$@\"\n")
    LAUNCHER.chmod(0o755)
    # WorkingDirectory takes a path value; ExecStart follows systemd word quoting.
    UNIT.write_text("[Unit]\nDescription=Local voice input\nAfter=graphical-session.target\n"
                    "PartOf=graphical-session.target\n\n[Service]\nType=simple\n"
                    f"WorkingDirectory={str(ROOT).replace('%', '%%')}\n"
                    f"ExecStart={json.dumps(str(python))} -m voice_input.cli serve --background\n"
                    "Restart=on-failure\nRestartSec=2\nTimeoutStopSec=8\n\n"
                    "[Install]\nWantedBy=graphical-session.target\n")
    APPLICATION.write_text("[Desktop Entry]\nType=Application\nName=语音输入\n"
                           "Comment=本地 Whisper 语音转文字\n"
                           f"Exec={json.dumps(str(LAUNCHER))} show\n"
                           "Icon=audio-input-microphone\nTerminal=false\n"
                           "Categories=Utility;Accessibility;\nStartupNotify=false\n")
    for action, (name, binding) in BINDINGS.items():
        path = shortcut_path(action)
        shortcut = Gio.Settings.new_with_path(CUSTOM, path)
        shortcut.set_string("name", name)
        shortcut.set_string("command", shlex.quote(str(LAUNCHER)) + " " + action)
        shortcut.set_string("binding", binding)
        if path not in paths:
            paths.append(path)
    settings.set_strv("custom-keybindings", paths)
    Gio.Settings.sync()
    names = [name for name in ["DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE"] if name in os.environ]
    subprocess.run(["systemctl", "--user", "import-environment", *names], check=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "voice-input.service"], check=True)
    subprocess.run(["systemctl", "--user", "is-active", "--quiet", "voice-input.service"], check=True)
    print("已安装：应用菜单里的“语音输入”；Ctrl+Alt+空格开始／结束，Ctrl+Alt+Shift+空格取消")


def uninstall():
    subprocess.run(["systemctl", "--user", "disable", "--now", "voice-input.service"], check=False)
    settings = Gio.Settings.new(SCHEMA)
    own_paths = {shortcut_path(action) for action in BINDINGS}
    settings.set_strv("custom-keybindings", [p for p in settings.get_strv("custom-keybindings") if p not in own_paths])
    for path in own_paths:
        shortcut = Gio.Settings.new_with_path(CUSTOM, path)
        for key in ["name", "command", "binding"]:
            shortcut.reset(key)
    Gio.Settings.sync()
    for target in [UNIT, APPLICATION, LAUNCHER]:
        target.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print("已卸载用户服务、启动器和本应用快捷键；源码、模型与配置保留")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    arguments = parser.parse_args()
    try:
        uninstall() if arguments.uninstall else install()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
