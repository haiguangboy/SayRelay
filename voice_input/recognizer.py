import time

from opencc import OpenCC

from .audio import signal_metrics
from .punctuation import Punctuator


class Recognizer:
    """One resident CPU model, called from a single worker."""

    def __init__(self, config):
        from faster_whisper import WhisperModel
        self.simplifier = OpenCC("t2s")
        self.model = WhisperModel(str(config.model_path), device="cpu", compute_type="int8",
                                  cpu_threads=config.threads, num_workers=1, local_files_only=True)
        self.punctuator = Punctuator()

    def transcribe(self, pcm, language="zh", punctuation=True, decoding="fast", vocabulary=""):
        metrics = signal_metrics(pcm)
        if metrics["audio_seconds"] < 0.3:
            raise ValueError("录音太短，请说完一句话再停止")
        if metrics["rms"] < 0.001:
            return {"text": "", "recognition_seconds": 0.0, **metrics}
        started = time.perf_counter()
        segments, _ = self.model.transcribe(
            pcm, language=None if language == "auto" else language,
            task="transcribe", beam_size=5 if decoding == "careful" else 1,
            best_of=1, temperature=0, hotwords=vocabulary.strip() or None,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 350},
            condition_on_previous_text=False, without_timestamps=True,
        )
        # Whisper's zh language flag does not select a Chinese writing system.
        # Normalize the completed transcript once, before any display or delivery.
        text = self.simplifier.convert("".join(segment.text for segment in segments).strip())
        punctuation_started = time.perf_counter()
        if punctuation:
            text = self.punctuator.restore(text)
        punctuation_seconds = time.perf_counter() - punctuation_started
        return {"text": text, "recognition_seconds": time.perf_counter() - started,
                "punctuation_seconds": punctuation_seconds, **metrics}
