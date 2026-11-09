import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voice-input"
CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "voice-input"
SOCKET_PATH = RUNTIME_DIR / "control.sock"


@dataclass
class Config:
    model: str = "small"
    threads: int = 4
    source: str = ""
    input_mode: str = "local"
    output: str = "paste"
    language: str = "zh"
    punctuation: bool = True
    decoding: str = "fast"
    vocabulary: str = ""
    max_record_seconds: int = 120

    def validate(self):
        if self.input_mode not in {"local", "remote"}:
            raise ValueError("输入来源应为 local 或 remote")
        if self.model not in {"base", "small"}:
            raise ValueError("模型应为 base 或 small")
        if not isinstance(self.threads, int) or not 1 <= self.threads <= 16:
            raise ValueError("线程数应为 1–16")
        if self.output not in {"paste", "terminal", "clipboard"}:
            raise ValueError("输出方式应为 paste、terminal 或 clipboard")
        if self.language not in {"zh", "en", "auto"}:
            raise ValueError("语言应为 zh、en 或 auto")
        if not isinstance(self.punctuation, bool):
            raise ValueError("自动标点应为开启或关闭")
        if self.decoding not in {"fast", "careful"}:
            raise ValueError("识别方式应为 fast 或 careful")
        if not isinstance(self.vocabulary, str) or len(self.vocabulary) > 400:
            raise ValueError("常用词应为不超过 400 字符的文本")
        if not isinstance(self.source, str) or len(self.source) > 512:
            raise ValueError("无效麦克风名称")
        if not isinstance(self.max_record_seconds, int) or not 1 <= self.max_record_seconds <= 300:
            raise ValueError("录音上限应为 1–300 秒")
        return self

    @property
    def model_path(self):
        return ROOT / "models" / self.model

    def save(self):
        self.validate()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(CONFIG_FILE)

    @classmethod
    def load(cls):
        if not CONFIG_FILE.exists():
            return cls()
        return cls(**json.loads(CONFIG_FILE.read_text())).validate()
