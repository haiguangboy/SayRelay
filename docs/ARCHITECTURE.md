# 架构记录

## 目标

本项目把 Ubuntu 本机麦克风或小米遥控器语音转换成当前桌面输入框中的文字：

```text
本机麦克风 audio.py / 小米遥控器 remote.py + atvv.py
        ↓ PCM 16 kHz 单声道 float32
recognizer.py（本地 faster-whisper）
        ↓ 原始文字
OpenCC 简体转换 → punctuation.py 本地标点
        ↓ 最终文字
delivery.py / keyboard.py → 当前焦点输入框
```

服务和 UI 通过 `service.py` 汇合，CLI 和 Unix socket 都只调用这一层。输入源与输出交付因此可以独立替换。

## 模块边界

- `audio.py`：PipeWire / 系统默认麦克风，生成符合识别器要求的 PCM 音频。
- `remote.py`：BlueZ GATT 发现、通知订阅、ATVV 协商、麦克风控制和退避重试。
- `atvv.py`：ATVV 1.0 / 16 kHz 能力解析和连续 ADPCM 音频解码。
- `remote_buttons.py`：精确匹配 RC003 的 evdev 按下/松开、重复过滤与设备独占。
- `recognizer.py`：常驻 CPU 模型、语言设置、快速/精细解码和可选词表。
- `punctuation.py`：只添加标点，不重写正文；保护英文、路径、版本号和已有标点。
- `delivery.py`：剪贴板和桌面通知。
- `keyboard.py`：通过 xdg-desktop-portal 发送 Ctrl+V / Ctrl+Shift+V，并持久化恢复授权。
- `service.py`：录音状态机、异步任务、取消、失败保留和本地 socket 接口。
- `ui.py`：GTK 设置窗口，不负责识别算法。
- `install.py`：用户级 launcher、systemd 服务和 GNOME 快捷键安装。
- `tools/remote_input_access.py`：一次性系统设备授权，仅安装匹配 RC003 的 udev 规则。

## 状态与可靠性

录音、转写、交付是显式状态。任何阶段失败都必须保留已经生成的文字和剪贴板内容，并通过窗口状态与持续通知告知用户。自动粘贴只发送粘贴快捷键；遥控器确定键是独立的 Enter 动作，目标均为当前焦点窗口。

键盘授权采用 portal v2 的持久权限和恢复令牌。服务启动时尝试恢复，使用前再次检查；恢复失败时先提示授权，不启动录音。

## 小米遥控器接入

`Config.input_mode` 默认为 `local`，旧配置兼容；`remote` 时同时启用 BlueZ 音频与设备专属按键适配器。切换只允许在空闲时进行，并保留原 PipeWire `source` 选择。Controller 的 `capture` 指向实际音源，后续识别、取消和交付复用同一条流水线。

本机 RC003 的语音键产生 F5，未产生预期的 ATVV START_SEARCH，因此实际按键由 evdev 驱动，GATT 的重复开始请求不再二次切换录音。只打开蓝牙 `2717:32B8` 的输入节点，并用 EVIOCGID 再次验证身份，再执行 EVIOCGRAB；不打开电脑键盘。按下录音、松开转写，内核重复事件被忽略；按键断连或 SYN_DROPPED 丢弃当前录音。确定键将 Linux KEY_ENTER 转为桌面 portal 的一次 Return 按下/释放；仅空闲且键盘授权就绪时执行，按住语音键期间也拒绝，按住确定键不连发。忙碌或恢复授权后不会补发旧动作，失败不重试；状态接口提供物理按键次数与最近 Enter 结果，不宣称目标应用已发送消息。其余遥控器按键暂不执行动作，关闭文件描述符即恢复原生事件。

首次通过 pkexec / sudo 安装固定 udev 规则，让 logind 为活动桌面用户授予这一设备的 ACL；运行服务仍为普通用户。规则安装与桌面自动粘贴的 portal 权限是两件独立的系统授权。

BlueZ 仅对已确认的 ATVV TX 写入命令，动态查找特征路径；先完成两路通知订阅，再请求能力。连接以 generation 标记异步回复，切换或断连后忽略旧回复。音频只留内存，先冻结 PCM 再提交识别线程。新录音在明确的 16 kHz AUDIO_START 后以 predictor/index=0 初始化，兼容固件省略 AUDIO_SYNC；收到 AUDIO_SYNC 时重置解码状态。既无开始消息也无同步信息的音频不被当作有效录音。断连、持续无音频、格式错误均丢弃未完成结果。

固件可直接主动开流，按下后先等待 180 ms；没有收到开始消息才补发 MIC_OPEN，避免重复开启重置编码器。新的流 ID 会重置解码状态；GATT 早于 evdev 的事件仅短暂保留，仍需物理按键确认才创建任务，迟到事件不会自行重启已取消的录音。

## 参考项目关系

本项目参考了 Axonkey、Mi-Ao 和 SayAll 的产品方向与交互思路。`atvv.py` 的协议与解码算法改编自 Mi-Ao，保留其 MIT 版权和许可，见 `licenses/mi-ao-MIT.txt`。新流初始化行为也与 Axonkey 的公开实现交叉核对。BlueZ、Linux 按键与 Ubuntu/Wayland 集成为本项目实现。
