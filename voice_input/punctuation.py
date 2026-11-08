import re

from .config import ROOT

MODEL_PATH = ROOT / "models" / "punctuation" / "model.int8.onnx"
MARKS = frozenset("，。？！、；：,.?!;:")
CHINESE_MARKS = str.maketrans(",.?!;:", "，。？！；：")
ASCII_TOKEN = re.compile(r"[A-Za-z0-9_./:@~+%#?=&\\-]+")
HAN = re.compile(r"[\u3400-\u9fff]")


class Punctuator:
    """Predict punctuation locally; retain the original transcript's characters."""

    def __init__(self, threads=2):
        import sherpa_onnx

        if not MODEL_PATH.is_file():
            raise ValueError(f"本地标点模型不存在：{MODEL_PATH}")
        config = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=str(MODEL_PATH), num_threads=threads, provider="cpu"))
        self.model = sherpa_onnx.OfflinePunctuation(config)

    def restore(self, text):
        if not text or not HAN.search(text):
            return text
        return merge_punctuation(text, self.model.add_punctuation(text))


def merge_punctuation(original, predicted):
    # sherpa may respell whitespace around ASCII tokens. Transfer only marks
    # onto the original character offsets, never its reconstructed wording.
    positions = [i for i, char in enumerate(original) if not char.isspace() and char not in MARKS]
    expected = "".join(original[i] for i in positions)
    actual = "".join(char for char in predicted if not char.isspace() and char not in MARKS)
    if expected != actual:
        return original

    suggestions = {}
    index = -1
    for char in predicted:
        if char in MARKS:
            if index >= 0:
                suggestions[index] = char.translate(CHINESE_MARKS)
        elif not char.isspace():
            index += 1

    protected = list(ASCII_TOKEN.finditer(original))
    insertions = {}
    for index, mark in suggestions.items():
        offset = positions[index] + 1
        next_offset = positions[index + 1] if index + 1 < len(positions) else len(original)
        gap = original[offset:next_offset]
        if gap.strip() or "\n" in gap or "\r" in gap:
            continue
        if any(token.start() < offset < token.end() for token in protected):
            continue
        insertions[offset] = mark
    return "".join(char + insertions.get(i + 1, "") for i, char in enumerate(original))
