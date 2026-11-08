"""PipeWire microphone capture; returns PCM WAV for a source-independent recognizer."""

import os
import signal
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from .config import RUNTIME_DIR


def read_pcm(path, max_seconds=300):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("接口需要 16 kHz、单声道、16-bit PCM WAV")
        frames = audio.getnframes()
        if not 0 < frames <= max_seconds * 16000:
            raise ValueError(f"音频应为 0–{max_seconds} 秒")
        raw = audio.readframes(frames)
        if len(raw) != frames * 2:
            raise ValueError("WAV 音频不完整")
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def signal_metrics(pcm):
    return {
        "audio_seconds": len(pcm) / 16000,
        "rms": float(np.sqrt(np.mean(pcm * pcm))) if len(pcm) else 0.0,
        "peak": float(np.max(np.abs(pcm))) if len(pcm) else 0.0,
    }


class Microphone:
    def __init__(self):
        self.process = None
        self.path = None
        self.started = None
        self.log = None

    def start(self, source=""):
        if self.process is not None:
            raise RuntimeError("已经在录音")
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="recording-", dir=RUNTIME_DIR)
        os.close(fd)
        self.path = Path(name)
        self.log = tempfile.TemporaryFile()
        command = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16",
                   "--media-role", "Communication"]
        if source:
            command += ["--target", source]
        command.append(str(self.path))
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=self.log)
            self.started = time.monotonic()
        except Exception:
            self.discard()
            raise

    def check(self):
        if self.process is not None and self.process.poll() is not None:
            self.log.seek(0)
            detail = self.log.read(2048).decode(errors="replace").strip()
            raise RuntimeError(f"麦克风录音已停止，请检查输入设备：{detail}")

    def stop(self):
        if self.process is None:
            raise RuntimeError("当前没有录音")
        process = self.process
        try:
            self.check()
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
                raise RuntimeError("麦克风未能正常结束录音")
            # Ubuntu's pw-record returns 1 on an intentional SIGINT even for a
            # finalized, valid WAV. Early exits are caught by check() above.
            if process.returncode not in (0, 1, -signal.SIGINT, 128 + signal.SIGINT):
                raise RuntimeError("麦克风录音失败")
            return read_pcm(self.path)
        finally:
            self.discard()

    def discard(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process = None
        if self.path:
            self.path.unlink(missing_ok=True)
        self.path = None
        if self.log:
            self.log.close()
        self.log = None


def sources():
    import json
    completed = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                               capture_output=True, text=True, check=True, timeout=5)
    return [(item["name"], item.get("description", item["name"]))
            for item in json.loads(completed.stdout) if not item["name"].endswith(".monitor")]
