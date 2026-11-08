# Download local models

[中文说明](../models/README.md)

Run these commands from the repository root after installing Python dependencies. Downloads require access to Hugging Face and GitHub; public models need no API key. Model weights are excluded from Git.

## Speech recognition

Download the default multilingual `small` model at the pinned revision:

```bash
.venv/bin/hf download Systran/faster-whisper-small \
  config.json model.bin tokenizer.json vocabulary.txt \
  --revision 536b0662742c02347bc0e980a01041f333bce120 \
  --local-dir models/small
```

The optional, smaller `base` model is used by the fast model setting:

```bash
.venv/bin/hf download Systran/faster-whisper-base \
  config.json model.bin tokenizer.json vocabulary.txt \
  --revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 \
  --local-dir models/base
```

Each directory contains a `source.json` with upstream metadata. `small` is required by the current installer even if you intend to switch to `base` afterward.

## Punctuation

Download the pinned sherpa-onnx CT-Transformer INT8 archive, verify its checksum, then copy the model into place:

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

The source URL, archive checksum, and model checksum are recorded in `models/punctuation/source.json`. Allow extra space for the archive and extracted files. The temporary download directory can be removed after a successful copy.

## Verify before use

```bash
sha256sum --check models/SHA256SUMS.default
# Only if you downloaded base:
sha256sum --check models/SHA256SUMS.base
```

Stop if any checksum fails and check the source and revision. Once the required files pass verification, return to the [installation instructions](../README.md#install-from-source).

Weights retain their upstream licensing terms; see [third-party notices](../THIRD_PARTY_NOTICES.md). They are downloaded directly from the upstream hosts and are not bundled with SayRelay's source.
