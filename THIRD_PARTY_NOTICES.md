# 第三方组件与模型

本仓库中的 Python 应用代码以 MIT License 发布。运行时依赖及模型由各自的上游项目负责许可和维护，使用者应同时遵守它们的许可证和服务条款。

- 直接依赖的版本固定在 `pyproject.toml`。本机包元数据显示：faster-whisper / CTranslate2 / ONNX Runtime 使用 MIT，PyAV 使用 BSD-3-Clause，NumPy 声明 BSD-3-Clause 等多个组件许可，OpenCC Python 重实现 / sherpa-onnx 使用 Apache-2.0。实际发行包可能包含 FFmpeg 等额外二进制组件，应以分发文件内的许可证为准；本清单尚未覆盖所有传递依赖。
- `models/base`、`models/small`：Systran 的 faster-whisper CTranslate2 权重，下载来源和元数据见对应 `source.json`。权重没有放入 Git 仓库。
- `models/punctuation`：sherpa-onnx 发布的 CT-Transformer 中文/英文标点模型，原始来源、校验和和生成标点范围见 `models/punctuation/source.json`。
- `voice_input/atvv.py` 的协议解析与 ADPCM 解码改编自 [Mi-Ao](https://github.com/fanxeon/mi-ao) 的 `ATVVProtocol.swift`、`ADPCMDecoder.swift`，语音事件处理参考 `BLEVoiceBridge.swift`。Copyright (c) 2026 FanXeon@Poemcoder with Codex，MIT；完整版权与许可保留在 `licenses/mi-ao-MIT.txt`，源码与 wheel 均包含该文件。

发布二进制安装包时，应把这些上游许可证和模型条款一起放入安装包，并在发布页说明模型下载来源和校验和。

当前 Systran 模型来源元数据标注为 MIT；标点权重的确切再分发条件需要在捆绑安装包前核实。本仓库只提供权重来源和下载指引，不捆绑这些权重。
