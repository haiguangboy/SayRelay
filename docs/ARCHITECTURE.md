# 架构记录

## 目标

本项目把 Ubuntu 本机麦克风转换成当前桌面输入框中的文字：

```text
麦克风 / 未来的小米蓝牙遥控器
        ↓ PCM 16 kHz
audio.py
        ↓ WAV / 流式片段
recognizer.py（本地 faster-whisper）
        ↓ 原始文字
OpenCC 简体转换 → punctuation.py 本地标点
        ↓ 最终文字
delivery.py / keyboard.py → 当前焦点输入框
```

服务和 UI 通过 `service.py` 汇合，CLI 和 Unix socket 都只调用这一层。输入源与输出交付因此可以独立替换。

## 模块边界

- `audio.py`：PipeWire / 系统默认麦克风，生成符合识别器要求的 PCM 音频。
- `recognizer.py`：常驻 CPU 模型、语言设置、快速/精细解码和可选词表。
- `punctuation.py`：只添加标点，不重写正文；保护英文、路径、版本号和已有标点。
- `delivery.py`：剪贴板和桌面通知。
- `keyboard.py`：通过 xdg-desktop-portal 发送 Ctrl+V / Ctrl+Shift+V，并持久化恢复授权。
- `service.py`：录音状态机、异步任务、取消、失败保留和本地 socket 接口。
- `ui.py`：GTK 设置窗口，不负责识别算法。
- `install.py`：用户级 launcher、systemd 服务和 GNOME 快捷键安装。

## 状态与可靠性

录音、转写、交付是显式状态。任何阶段失败都必须保留已经生成的文字和剪贴板内容，并通过窗口状态与持续通知告知用户。自动粘贴只发送粘贴快捷键，不发送回车，也不主动切换焦点。

键盘授权采用 portal v2 的持久权限和恢复令牌。服务启动时尝试恢复，使用前再次检查；恢复失败时先提示授权，不启动录音。

## 小米遥控器接入计划

遥控器接入应新增独立适配器，负责蓝牙 HID / 音频设备发现、按键事件和音频采集，不把蓝牙协议细节放进识别或交付模块。首个可交付版本可先复用系统麦克风完成端到端体验，再把遥控器的按键映射到现有 `toggle` / `cancel` 动作，并根据实际设备确认它是音频输入设备、遥控按键设备还是两者的组合。

## 参考项目关系

本项目参考了 Axonkey、Mi-Ao 和 SayAll 的产品方向与交互思路，没有复制它们的应用代码。它们的模型、输入法和平台权限实现不同；这里保留 Ubuntu/Wayland 的本地实现，后续再补充小米蓝牙遥控器适配。
