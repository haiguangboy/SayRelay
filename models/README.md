# 本地模型

模型权重没有提交到 Git 仓库。这样可以避免公开仓库超过常规托管平台的文件限制，也让使用者按自己的 CPU、磁盘和许可要求选择模型。

应用默认需要 `models/small`，可选 `models/base`；每个目录应包含对应的 faster-whisper CTranslate2 文件：`model.bin`、`config.json`、`tokenizer.json` 和 `vocabulary.txt`。来源与上游元数据保存在同目录的 `source.json`。

标点功能还需要 `models/punctuation/model.int8.onnx`。该文件的来源、SHA-256 和上游说明见 `models/punctuation/source.json` 与 `UPSTREAM_README.md`。

## 固定版本下载

在项目根目录、Python 依赖安装完毕后执行。默认识别模型约 484 MB，标点模型约 76 MB；初次下载还会用到临时磁盘空间。公开模型无需 API 密钥，网络需能访问 Hugging Face 和 GitHub。

```bash
.venv/bin/hf download Systran/faster-whisper-small \
  config.json model.bin tokenizer.json vocabulary.txt \
  --revision 536b0662742c02347bc0e980a01041f333bce120 \
  --local-dir models/small
```

可选的快速档 base：

```bash
.venv/bin/hf download Systran/faster-whisper-base \
  config.json model.bin tokenizer.json vocabulary.txt \
  --revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 \
  --local-dir models/base
```

标点模型从 sherpa-onnx 的固定发布附件下载；先校验压缩包，再提取权重：

```bash
model_download_dir=$(mktemp -d)
curl --fail --location --retry 2 \
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2' \
  --output "$model_download_dir/punctuation.tar.bz2"
printf '%s  %s\n' \
  c0d5aa5f8eeb686032345e180bedf39319dc2e0556781c6264bcadba8328a6e1 \
  "$model_download_dir/punctuation.tar.bz2" | sha256sum --check && \
tar -xjf "$model_download_dir/punctuation.tar.bz2" -C "$model_download_dir" && \
cp "$model_download_dir/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8/model.int8.onnx" \
  models/punctuation/model.int8.onnx
```

下载完成后从项目根目录校验默认所需文件：

```bash
sha256sum --check models/SHA256SUMS.default
# 如果还下载了 base：
sha256sum --check models/SHA256SUMS.base
```

然后执行：

```bash
.venv/bin/python install.py
.venv/bin/python -m unittest discover -s tests -v
```

不需要把模型上传到本项目的 Git 仓库；后续产品发布可以使用独立的模型下载包或发布附件。
