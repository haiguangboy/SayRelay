"""ATVV 1.0 / 16 kHz audio used by Xiaomi RC003 firmware 2671.

Protocol and IMA decoder adapted from Mi-Ao (MIT), copyright (c) 2026
FanXeon@Poemcoder with Codex. See licenses/mi-ao-MIT.txt.
"""

from array import array

import numpy as np

SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
TX_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
RX_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
CONTROL_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"
GET_CAPS = bytes.fromhex("0a 01 00 00 03 03")

STEPS = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34,
    37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
    544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552,
    1707, 1878, 2066, 2272, 2494, 2740, 3008, 3307, 3638, 4002, 4402, 4842, 5327, 5860,
    6446, 7091, 7800, 8580, 9438, 10382, 11420, 12562, 13818, 15200,
    16720, 18392, 20231, 22254, 24479, 26927, 29620, 32767,
)
INDEX_DELTA = (-1, -1, -1, -1, 2, 4, 6, 8)


class ADPCMDecoder:
    """Continuous, high-nibble-first IMA ADPCM; reset at AUDIO_SYNC."""

    def __init__(self, predictor, index):
        if not -32768 <= predictor <= 32767 or not 0 <= index <= 88:
            raise ValueError("无效的遥控器音频同步参数")
        self.predictor = predictor
        self.index = index

    def decode(self, data):
        output = array("h")
        for byte in data:
            for nibble in (byte >> 4, byte & 15):
                step = STEPS[self.index]
                difference = step >> 3
                if nibble & 4:
                    difference += step
                if nibble & 2:
                    difference += step >> 1
                if nibble & 1:
                    difference += step >> 2
                self.predictor = max(-32768, min(32767, self.predictor +
                                                (-difference if nibble & 8 else difference)))
                self.index = max(0, min(88, self.index + INDEX_DELTA[nibble & 7]))
                output.append(self.predictor)
        return output


def capabilities(data):
    if len(data) < 7 or data[:3] != b"\x0b\x01\x00":
        raise ValueError("当前仅支持 ATVV 1.0 的小米遥控器（RC003 / 固件 2671）")
    codecs, interaction = data[3:5]
    if not codecs & 3 and interaction & 3:
        codecs, interaction = interaction, codecs
    if not codecs & 2:
        raise ValueError("遥控器未提供 16 kHz ADPCM 音频")
    size = int.from_bytes(data[5:7], "big")
    if not 1 <= size <= 512:
        raise ValueError("无效的遥控器音频帧长")
    return {"version": "1.0", "sample_rate": 16000, "frame_bytes": size,
            "interaction": interaction}


class AudioStream:
    def __init__(self, max_seconds=300):
        self.decoder = None
        self.samples = array("h")
        self.limit = max_seconds * 16000
        self.packets = 0
        self.unsynced_packets = 0
        self.start_received = False
        self.sync_count = 0

    def begin(self):
        # RC003 firmware 2671 may omit AUDIO_SYNC entirely. Mi-Ao and Axonkey
        # initialize the continuous decoder to zero for a new stream. Accept
        # that initialization only after the explicit 16 kHz AUDIO_START.
        if not self.start_received:
            if self.decoder is None:
                self.decoder = ADPCMDecoder(0, 0)
            self.start_received = True

    def sync(self, data):
        if len(data) < 7 or data[:2] != b"\x0a\x02":
            raise ValueError("遥控器音频同步格式不受支持")
        self.decoder = ADPCMDecoder(int.from_bytes(data[4:6], "big", signed=True), data[6])
        self.sync_count += 1

    def append(self, data):
        if not data or len(data) > 512:
            raise ValueError("无效的遥控器音频包")
        if self.decoder is None:
            self.unsynced_packets += 1
            return
        if len(self.samples) + len(data) * 2 > self.limit:
            raise ValueError("遥控器录音超过音频容量上限")
        self.samples.extend(self.decoder.decode(data))
        self.packets += 1

    def pcm(self):
        if self.unsynced_packets:
            raise ValueError("遥控器音频同步丢失，本次录音已丢弃，请重试")
        return np.asarray(self.samples, dtype=np.float32) / 32768
