# SayRelay · Ubuntu 本地语音输入

[English](README.md) · 简体中文

Ubuntu 本机麦克风 → 本地 Whisper → 简体中文与标点 → 当前应用输入框。

当前为 **0.1.0 原型**，产品名为 SayRelay。支持本机麦克风，小米蓝牙遥控器适配尚未实现。代码使用 MIT 许可证，依赖和模型分别遵守上游许可。当前命令、服务名和配置路径仍使用 `voice-input`，安装位置保持不变。

项目文档：[架构与模块](docs/ARCHITECTURE.md) · [产品路线](docs/PRODUCT_PLAN.md) · [版本记录](CHANGELOG.md) · [开发协作](CONTRIBUTING.md) · [发布流程](docs/RELEASE.md) · [第三方组件](THIRD_PARTY_NOTICES.md)。

[GitHub Actions](https://github.com/haiguangboy/SayRelay/actions/workflows/tests.yml) 首次远程运行已通过：Ubuntu 24.04 上的 25 项无模型测试通过，5 项依赖本地模型的测试跳过。含模型的本机环境共 30 项通过；远程测试不验证麦克风、桌面授权或实际输入框粘贴。

本机录音、转写、取消，以及原生 Wayland / XWayland 输入框粘贴验证已通过，详见 [验证记录](VALIDATION.md)。Codex 和其他实际应用的日常体验仍需要用户试用。

首版面向 Ubuntu 24.04 / GNOME Wayland。按 `Ctrl+Alt+空格` 开始录音，再按一次结束、转写并粘贴；`Ctrl+Alt+Shift+空格` 取消。先点选 Codex 或其他应用的输入框，转写期间保持焦点。只粘贴，不按回车发送。

应用菜单搜索“语音输入”可打开设置窗口，选择麦克风、small / base 模型和输出方式。关闭窗口后服务继续运行；“退出服务”会停止本次进程。服务随图形登录启动，快捷键也可以重新启动服务。

首次使用，在窗口点“启用自动粘贴”，随后在 Ubuntu 系统对话框中保留“记住此决定”并允许键盘控制。程序向 xdg-desktop-portal v2 请求持久授权，将系统返回的恢复令牌保存在本用户配置目录（文件权限 0600），服务重启后自动尝试恢复，不需要每次重新允许。系统撤销授权、令牌失效或未勾选记住时仍可能弹窗；程序不能替用户批准系统授权。采用键盘会话，无需 root、不读写 `/dev/uinput`，也不接入屏幕录制或鼠标控制。系统可能以“远程桌面”描述这项本机键盘能力。本程序没有网络监听接口。

选择自动粘贴输出时，每次录音前先检查键盘会话；若未就绪，先连接或打开系统授权，并明确通知“本次未开始录音”。完成后切回目标输入框，再按一次快捷键开始。录音中途若会话失效，识别文字仍会保留并复制，同时用持续通知明确提醒“未自动粘贴：文字已保留”和手动粘贴快捷键。选择“只复制”则无需键盘授权，可以正常录音。仅有会话结束时不会立刻重连；下次使用才尝试恢复，以免主动断开后反复连接。

输出选项：

- 应用：复制识别文字并发送 `Ctrl+V`，适用于 Codex 桌面、编辑器、浏览器等可粘贴输入框。
- 终端：使用 `Ctrl+Shift+V`，适用于常见终端中的 Codex CLI。
- 只复制：手动粘贴，识别结果也在窗口里可查看。录音中途键盘会话失效时会退回复制并持续提醒。

自动粘贴针对当前焦点，不主动寻找、切换或验证某个 Codex 会话；应用自定义快捷键、非文本控件或失去焦点均可能导致粘贴没有进入预期位置。快捷键模式不会弹出主窗口抢焦点。首次授权请先在设置窗口完成，再切回目标输入框。麦克风录音使用系统默认输入，也可在设置中指定输入设备；更换设备后重新打开窗口可刷新设备列表。

识别使用 small 多语言版、CPU INT8、4 线程、常驻加载，快速档为 base。中文转写先通过本地 OpenCC `t2s` 统一转成简体，再用本地 CT-Transformer INT8 标点模型补充逗号、句号、问号等，最后显示、复制、粘贴或由文件接口返回。标点模型只贡献标点位置，保留识别正文、原有空格、标点、换行、英文词、路径与版本号；不改写句子或修正错字。纯英文沿用 Whisper 输出。可在窗口取消勾选“自动添加中文标点”，也可用 `voice-input configure --punctuation off` 关闭、`--punctuation on` 开启。自动断句仍可能不符合说话人的意图，请在发送前校对。

中英混说中的人名、缩写、公司名可能被 small 模型听错；`zh` 表示主要语言，不禁止英文输出，`auto` 也不保证逐词识别语言。窗口的“识别方式与常用词”提供可选精细识别（beam size 5）和常用词提示（faster-whisper hotwords）。默认继续使用快速识别（beam size 1）、空词表。精细识别会增加等待，常用词提示可能影响其他词或造成漏句；本机公开中文长录音已观察到这类退化，因此只建议在针对性的短句复测中使用少量词，不将其视为可靠的纠错或强制替换。现有录音完成后会删除，无法仅凭最后文字重新比较中英识别准确率。

静音先经信号检查和 VAD，识别不到语音不粘贴。应用开启 VAD，因此实际延时与之前关闭 VAD 的纯模型基准不完全相同。录音最多 120 秒，届时自动停止并识别。两次录音之间等待上一次处理完成，取消后丢弃待交付结果。原始录音存于本用户运行目录，正常结束、取消或退出后删除；没有持久化文字历史，最近 20 个任务只留在进程内存中。剪贴板按正常桌面规则保留最近交付的文字；取消发生在复制之后时，取消的是待执行粘贴。

识别模型使用 Systran 的 faster-whisper 转换权重，不需要云 API 或密钥。Git 仓库仅保留模型来源元数据，权重需要[单独下载](models/README.md)。普通使用不发送音频到网络。此首版未实现小米蓝牙遥控器接入；后续可把遥控器解码为同样的 PCM，再复用识别和交付模块。

本机启动与接口示例：

```bash
voice-input show
voice-input status
voice-input toggle
voice-input cancel
voice-input configure --output terminal
voice-input configure --output paste
voice-input configure --model base
voice-input configure --punctuation on

# 可选复测设置；不更换模型，配置立即生效
voice-input configure --decoding careful
voice-input configure --vocabulary 'Vance, Washington, D.C.'
# 复测后恢复快速识别和空词表
voice-input configure --decoding fast --vocabulary ''

# 文件转写默认只返回文字，不修改剪贴板；要求单声道、16 kHz、16-bit PCM WAV
voice-input transcribe /absolute/path/sample.wav --wait

# 明确要求时才把文件识别结果交付到当前输入框
voice-input transcribe /absolute/path/sample.wav --wait --deliver

# 其他本地程序可直接交付文字，无需经过录音或 ASR
voice-input insert '来自其他程序的文字'
printf '%s' '来自管道的文字' | voice-input insert -
```

控制接口是 `$XDG_RUNTIME_DIR/voice-input/control.sock` 的 Unix socket，目录权限 0700、socket 0600，只接受同 UID 客户端，无 HTTP 端口。每个连接一条 UTF-8 JSON 请求，以换行结尾，返回一行 JSON。例：`{"action":"start"}`、`{"action":"stop"}`、`{"action":"status"}`、`{"action":"transcribe","path":"/absolute/path/sample.wav"}`；异步请求返回 `job_id` 后，用 `{"action":"job","id":"…"}` 获取结果。文件请求默认不交付到剪贴板；设 `"deliver":true` 才交付。`{"action":"insert","text":"…"}` 则明确执行当前配置的交付。

实现分为 `audio.py`（PipeWire 录音与 PCM）、`recognizer.py`（常驻 ASR）、`punctuation.py`（本地标点及正文保护）、`delivery.py` / `keyboard.py`（剪贴板与系统键盘授权）、`service.py`（异步状态、取消与本地接口）、`ui.py`（GTK 设置窗口）。这些边界方便后续接遥控器音源、其他识别引擎和输出适配。没有复制三个参考项目的应用代码。

从源码安装，面向 Ubuntu 24.04 自带的 Python 3.12 和 GNOME 桌面。首次安装依赖和下载模型需要联网，日常识别使用本地模型：

```bash
git clone https://github.com/haiguangboy/SayRelay.git
cd SayRelay

sudo apt-get update
sudo apt-get install -y python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-3.0 pipewire-bin pulseaudio-utils wl-clipboard libnotify-bin curl

/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e .

# 按 models/README.md 下载 small 和标点模型；base 为可选模型。
.venv/bin/python install.py
.venv/bin/python -m unittest discover -s tests -v
```

建议在长期保留的位置克隆或解压项目。安装器会引用当前源码目录；这是源码安装方式，尚未提供 deb / AppImage 安装包。没有下载标点模型时，5 项真实模型测试会跳过，其余 25 项不需要模型、麦克风或桌面授权。模型测试通过不代表识别准确率已达产品标准。

`install.py` 安装到当前用户：`~/.local/bin/voice-input`、`~/.local/share/applications/voice-input.desktop`、`~/.config/systemd/user/voice-input.service`，并在现有 GNOME 自定义快捷键列表中追加本应用的两个快捷键，不替换其他快捷键。设置文件为 `~/.config/voice-input/config.json`。源码目录需要保持在原位置；移动后重新执行安装器。

卸载：

```bash
.venv/bin/python install.py --uninstall
```

卸载移除本应用的服务、启动器和快捷键；源码、模型、用户配置保留。诊断可用 `systemctl --user status voice-input.service` 和 `journalctl --user -u voice-input.service`。此原型的人工验收重点是实际声音、Codex 焦点与系统键盘授权，不应把纯 ASR 测试通过等同于桌面输入链路已经验收。

参考与致谢：[Axonkey](https://github.com/leowzz/axonkey)、[Mi-Ao](https://github.com/fanxeon/mi-ao)、[SayAll](https://github.com/GetSayAll/remote-mic-app-windows)。当前为独立 Ubuntu 实现，未复制它们的应用代码。
