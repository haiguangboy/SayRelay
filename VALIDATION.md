**2026 年 11 月 7 日，本机首版验证记录。**

Ubuntu 24.04 / GNOME 46 / Wayland，Core Ultra 7 255H，平衡模式。默认 small 多语言模型、CPU INT8、4 线程；应用开启 VAD，模型常驻。

已通过的验证：

- 真实本机 PipeWire 默认麦克风能录出 16 kHz、单声道、16-bit PCM WAV。短探测得到约 0.85 秒数据，正常结束后临时音频删除。
- 实际后台服务的录音 → 取消流程通过，任务标记为 cancelled，运行目录不残留本次录音。
- 公开中文录音通过本地接口完成转写，5.55 秒录音在一次运行中约 0.88 秒生成完整文字。含 VAD，与先前纯模型测试参数略有不同。该样本“达摩院”被写成“打摩院”，仍需人工校对。
- `tests/test_pipeline.py` 的 8 项测试通过：识别中取消、已复制但粘贴前取消、重复操作拦截、无键盘授权时只复制、终端输出路由、文件接口默认不改剪贴板、静音不调用 Whisper、拒绝错误采样率。
- Unix socket 为 0600，运行目录为 0700；错误 JSON、非对象请求和未知操作会返回错误。
- 用户级 systemd 服务配置验证通过；GNOME 中已安装两个自定义快捷键，保留其他应用快捷键。安装器同时检查 GNOME 系统键位与已有自定义键位冲突。
- GTK 设置界面已渲染并检查布局。
- 桌面系统键盘授权成功后，通过真实 portal 键盘事件，将 `语音输入粘贴测试 Voice Input 123` 粘贴进测试程序的原生 **GdkWaylandDisplay** 输入框，并读取控件文字确认完全一致。这一结果验证了系统粘贴事件和剪贴板交付，不只是模拟调用；没有发送回车。测试约 0.97 秒完成，包含测试等待和粘贴延迟，不是语音端到端延时。
- 同一桌面测试在 **X11Display / XWayland** 输入框中也通过，确认这台 Wayland 桌面上的两类应用窗口均可接收测试文字。

首次测试中系统授权会话曾结束，应用正确退回只复制；重新建立会话后继续验证。另发现 GNOME 下 `wl-copy` 会造成短暂焦点变化，桌面测试加入了等待剪贴板切换完成的时间；正式服务原本已在复制后等待 250 ms 再触发粘贴。实际快捷键使用时仍需保持目标输入框焦点。

同日修复中文转写可能输出繁体的问题：识别完成后，使用本地 OpenCC `t2s` 统一转为简体，再交给窗口、剪贴板、粘贴与文件接口。新增繁体与英文、路径混排的回归测试，现有 9 项测试全部通过。服务重启后重新转写此前含繁体输出的 15 秒公开中文音频，实际接口与窗口状态返回相同简体文字；证据存于 `verification-simplified.json`。此步骤转换字形，不负责修正识别错字。

原始材料为 `verification-core.json`、`verification-paste*.json`，桌面手动测试脚本为 `tests/desktop_smoke.py`。它会打开自己的输入框，仅在确认自身有焦点后启动交付，不向其他应用发送回车。测试时请勿切换窗口。

**11 月 7 日至 8 日，自动中文标点修复。**

原先直接连接 Whisper 的各段文本，small 模型在自然中文样本中基本不输出标点。代码没有主动删除标点。对带标点 `initial_prompt` 的对比发现：能改善部分结果，但有两种提示使 15 秒样本只输出第一句，因此没有把提示词作为正式修复。对比结果见 `verification-prompt-comparison.json`。

参考项目的职责不同：[米遥 WhisperTranscriber](https://github.com/fanxeon/mi-ao/blob/d6019bcede80ff71d225385b5c44b6f5f84a4832/Sources/MiAo/WhisperTranscriber.swift) 使用带标点的中文初始提示，再直接返回 Whisper 转写；[Axonkey README](https://github.com/leowzz/axonkey/blob/937711691bfdbcb3a19635544776fb77c4c5ed23/README.md) 明确将识别交给输入法或其他应用；[SayAll Windows README](https://github.com/GetSayAll/remote-mic-app-windows/blob/5410f6d30e1061607c8ccbbca7c6c7f1cc474951/README.md) 也接入豆包、微信输入法、Vokie 等工具。后两者的语音输入效果来自配套的识别工具，不能据此认定遥控器应用自身带了标点模型。

正式处理链为 Whisper → OpenCC 简体 → 本地 CT-Transformer INT8 标点 → 窗口与交付。模型来自 sherpa-onnx 官方 punctuation-models 发布包，源模型为达摩院 / FunASR 的中文标点模型。模型文件 75,519,198 字节，压缩包 SHA-256 已与官方 checksum.txt 核对；来源、摘要及支持的标点记录在 `models/punctuation/source.json`，上游说明保留为 `UPSTREAM_README.md`。使用 sherpa-onnx 2.0.1、CPU 2 线程，无云 API、无用户音频上传。

模型输出的逗号、句号、问号、顿号仅作为插入建议，按原始字符位置合并。保留现有文字、空格、换行和标点，阻止在 ASCII 单词、路径和版本号内部插入标点；如果去除空白和标点后与原文不一致，就返回原文。已发现模型直接输出会重新安排 ASCII 空格、重复现有中文标点，本地合并层已覆盖这两种实际情况。纯英文不经过该标点步骤，外部 `insert` 文字也不经过识别处理。窗口与 CLI 均可关闭自动标点。

平衡模式、相同公开音频，每段识别后单独重复标点处理 5 次的中位数：

| 录音时长 | 标点耗时中位数 | 完整识别过程中测得的标点耗时 |
| --- | --- | --- |
| 5.55 秒 | 4 ms | 8 ms |
| 15 秒 | 8 ms | 21 ms |
| 30 秒 | 21 ms | 39 ms |
| 70.47 秒 | 64 ms | 91 ms |

这是有限样本的本机测量，不是准确率评测。全部模型在一次进程中加载约 0.93 秒，测试峰值 RSS 约 916 MiB。`recognition_seconds` 包含标点步骤，`punctuation_seconds` 单独计时；两次完整 Whisper 运行的耗时差受 CPU 调度等影响，不应当作标点延时。公开样本输出与测量保存在 `verification-punctuation.json`。

现有 12 项流程 / 正文保护测试及 4 项真实标点模型测试全部通过，共 16 项。新增测试覆盖复合问句断句、技术文本字符和空格保留、已有标点不重复、英文保持、正文变动时拒绝采用模型文本。GTK 自动标点开关也通过初始化检查。标点步骤不纠正 Whisper 原有同音错字；语义断句仍需人工校对。

空闲时重启正式后台服务后，15 秒公开录音通过实际 Unix socket 文件接口返回了带逗号、句号、问号的简体文字，完整识别约 1.23 秒，其中标点约 28 ms；窗口最近文字与任务结果一致，文件接口没有交付剪贴板。证据为 `verification-punctuation-service.json`。服务默认已开启自动标点；重启后已重新完成系统键盘授权，最终状态为 `keyboard_ready=true`、`keyboard_connecting=false`。

尚需用户实际体验验收：在 Codex 对话框、浏览器输入框、文本编辑器和终端分别说话，检查真实词汇的错字率、焦点行为和主观等待。当前没有自动识别目标应用，因此终端需切换到 Ctrl+Shift+V 输出方式。没有接入小米遥控器，未进行长时间、休眠唤醒或多桌面环境回归。

**11 月 8 日，中英混说反馈与可选复测设置。**

用户实测中，Vance、Washington, D.C. 及若干公司名 / 专业词没有正确转写；New Yorker、CNN、Trump、NASDAQ 等英文则能出现在结果中。这说明当前模型具备中英输出能力，尚不足以可靠识别混说中的专有名词。原始录音已删除，本轮无法重放该用户样本，不能计算其错误率，也不能宣称某项调整已解决这些词。OpenCC 与标点步骤均没有将 Vance 替换成中文的逻辑。

新增可选设置 `decoding=careful`（beam size 5）与 `vocabulary`（最多 400 字符、传入 faster-whisper 的 hotwords）；窗口可编辑和保存，CLI / Unix socket 配置均支持。`language` 现也可由 CLI 设置。它们不触发模型重载。默认保留 `decoding=fast`、`vocabulary=""`，旧配置兼容。词表只影响声学解码，不做文本查找替换，未将不确定的 EMEDA、MARWELL、super-attentions 自动改成猜测的词。

公开中文样本的单次比较：beam size 5 在 15 / 30 秒样本上约 1.46 / 2.19 秒，原 beam size 1 约 1.15 / 1.49 秒；自动语言检测额外增加等待，并未改善这些纯中文样本。带英文热词的结果有词语变化，短词表 `Vance, Washington, D.C.` 在 70 秒自然中文样本中出现明显漏句，因此未自动启用。这些中文样本只能验证延时和回归风险，不能验证 Vance、Washington, D.C. 的混说识别效果。测量记录见 `verification-mixed-decoding.json`、`verification-mixed-vocabulary.json` 与 `verification-mixed-long.json`。

现有及新增 19 项测试通过。新增覆盖旧配置兼容、词表输入约束，以及模拟 ASR 已输出英文名字时，后续真实 OpenCC / 标点处理保留 Vance、Washington, D.C.、CNN、NASDAQ、Intel 的完整拼写；这个测试验证处理链，不验证语音模型能否从声音识别这些词。GTK 的设置初始化、精细识别保存、词表保存、未保存编辑保留和忙碌状态检查通过。

正式服务在空闲时重启并通过真实 CLI / Unix socket 配置接口复测，配置保持快速识别、空词表。5.55 秒公开中文文件转写约 0.92 秒，包含简体与标点，文件接口没有交付剪贴板；记录为 `verification-mixed-service.json`。重启后的键盘授权状态需以窗口或 `voice-input status` 为准。

待实测短句建议：分别口述“我今天读了 New Yorker 和 CNN 的报道”“我们讨论了 Trump 和 Vance”“这条消息来自 Washington, D.C.”，先用空词表，再用少量词表和精细识别比较。要提高开放词汇下的混说准确率，仍需评估更强的本地 ASR 模型，不能从标点准确推断识别准确。

**11 月 8 日，自动粘贴授权持久化与缺失提示修复。**

用户反馈服务重启或一段时间后需要重新授权，未粘贴容易误以为没有录上。现场状态为语音识别已完成、文字已复制，但 `keyboard_ready=false`，会话关闭消息为“系统已结束键盘授权”。systemd 显示当前服务无崩溃重启；前几次模型 / 标点更新确有主动重启。暂没有证据证明存在固定的“空闲超时”，不能把所有断开都归因于时间间隔。

旧代码请求 RemoteDesktop 时仅设置键盘设备，没有请求持久化，也没有保存系统返回的恢复令牌；服务重启必然结束旧 D-Bus 会话。实机 xdg-desktop-portal 1.18.4、GNOME 后端 46.2 的 RemoteDesktop 接口均为 v2。[该版本接口规范](https://github.com/flatpak/xdg-desktop-portal/blob/1.18.4/data/org.freedesktop.portal.RemoteDesktop.xml) 支持在 SelectDevices 设置 `persist_mode=2` 与 `restore_token`。[GNOME 实现](https://github.com/GNOME/xdg-desktop-portal-gnome/blob/46.2/src/remotedesktopdialog.c) 在该请求下显示并默认勾选“记住此决定”，首次仍由用户在系统窗口允许；后续可恢复已授予的权限。

现在请求持久化键盘权限，恢复令牌独立存于 `~/.config/voice-input/keyboard-restore-token`（文件 0600，新建目录 0700），不出现在状态 JSON、日志或验证材料中。一次性令牌在提交恢复前消耗，成功后保存返回令牌；兼容系统重复返回同一值。服务启动时若有令牌且输出方式需要键盘，则自动尝试恢复；会话结束时不立即循环重连，下次录音前才再次检查。系统撤销、令牌失效、未允许记住或旧版 portal 仍可能要求再次确认。程序不修改系统权限库，不替用户点击允许。

新增录音前检查：自动粘贴未就绪时先连接 / 请求系统授权，返回 `recording_started=false`，保持 idle，明确通知本次尚未录音，授权后再次按快捷键。只复制模式继续无需键盘权限录音。如果录音 / 转写中途会话失效或发送粘贴失败，则保留正文和剪贴板，使用 critical、无自动到期的通知明确说明“未自动粘贴：文字已保留”，附当前输出模式对应的粘贴快捷键。GNOME 最终如何显示通知仍受系统通知设置影响，窗口和任务状态同时保留结果。

30 项测试通过，覆盖 v2 键盘权限持久化请求、令牌保存与文件权限、重启读取与更新、恢复失败不重复使用已消耗令牌、v1 临时授权兼容、失效事件清理、启动时恢复、录音前拦截、只复制模式、识别中失去权限及粘贴发送失败后的文字保留与持续通知。

真实系统授权后，确认 `keyboard_ready=true`、`keyboard_remembered=true`，恢复令牌文件权限为 0600。13:48 在空闲状态主动重启正式 systemd 服务，进程 PID 从 331444 变为 332367；仅轮询状态，没有调用启用授权接口，约 0.88 秒后键盘会话已自动恢复且授权仍记住。模型此时还在独立加载；该数值仅表示本次服务重启及键盘恢复所用时间，不是完整应用启动或语音识别延时。证据存于 `verification-authorization.json`，不含恢复令牌。

恢复后的真实桌面测试再次将 `语音输入粘贴测试 Voice Input 123` 粘贴进独立的 Wayland 与 XWayland 测试输入框，读取控件文字确认完全一致，两次均约 0.96 秒完成；没有发送回车。证据分别存于 `verification-paste-gdkwaylanddisplay.json` 和 `verification-paste-x11display.json`。当前验证证明本机这次服务重启可自动恢复持久授权，不代表已经验证整机重启、长时间空闲或休眠唤醒。

**11 月 8 日，Git 仓库与公开源码准备。**

建立 `main` 分支，记录当前 0.1.0 原型基线。增加 README 导航、架构、产品路线、变更记录、贡献与发布流程、MIT 许可证和第三方说明；模型权重、虚拟环境、构建产物、本机测量 JSON、截图和音频不进入 Git。本文引用的原始 `verification-*.json` 留在开发机，公开源码只包含汇总结论。

模型下载指引固定 Systran 模型 revision 和 sherpa-onnx 标点附件，新增逐文件 SHA-256 清单；本机 small、base 与标点权重共 9 个文件均校验通过。尚未在新机器上重新下载和安装，下载指引不等同于新机安装验收。

本机含模型环境 30 项测试全部通过。从 Git 暂存树导出独立临时目录，不携带模型，25 项测试通过、5 项真实模型测试跳过。混合词汇的真实标点测试现明确依赖模型文件，避免干净仓库中的测试因缺失权重失败；测试中的示例路径改用 `/home/demo`。GitHub Actions 配置按 Ubuntu 24.04 系统 Python 与 PyGObject 设置，远程工作流尚未运行。

在干净导出目录中使用 `uv build` 构建 sdist 和 wheel 均成功。检查 sdist 包含安装器、文档、模型来源 / 校验清单和桌面测试；wheel 包含 MIT 许可和第三方说明。两者均不包含模型权重或录音。wheel 仅是 Python 包构建验证，尚非完整桌面安装包；对外源码分发优先使用包含整个仓库的源码归档。
