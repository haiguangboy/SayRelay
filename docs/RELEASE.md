# Git 与发布流程

## 当前仓库

默认分支为 `main`，版本为 `0.1.0`。首个提交只记录当前已存在的原型，历史调试过程见 `VALIDATION.md`；不补造此前的提交历史。

如果尚未指定个人提交身份，首个本地快照使用 `Local Voice Input snapshot <snapshot@localhost>`，表示自动整理的本地记录。正式发布前可根据维护者的实际 GitHub 身份设置后续提交署名；这不会改动系统全局 Git 配置。

仓库保留源码、测试、文档、安装器、模型来源和校验和。模型权重、虚拟环境、构建产物、本机验证 JSON 和截图均留在本机并被忽略。`VALIDATION.md` 引用的 `verification-*.json` 属于本机测量附件，克隆仓库不会包含这些附件；文档已汇总关键结论与测量限制。

## 后续开发

按功能提交，让每次提交都能说明行为变化和验证结果。Git 作者可仅对本项目设置：

```bash
git config --local user.name "你的署名"
git config --local user.email "你的 GitHub 隐私邮箱"
git status
git diff
```

## GitHub 仓库

公开仓库为 [haiguangboy/SayRelay](https://github.com/haiguangboy/SayRelay)。已经配置该远程仓库的本地工作目录可使用：

```bash
git remote -v
git push -u origin main
```

新环境可通过 GitHub CLI 登录 HTTPS，或使用已授权的 SSH 密钥进行推送。当前没有发布安装包，先用源码发布收集 Ubuntu 用户反馈。

## 每次发布的检查

1. 从 Git 导出干净目录，确认不含模型权重、私人配置、录音或授权令牌。
2. 在 Ubuntu 24.04 上按 README 重新安装，下载固定版本模型并校验 SHA-256。
3. 运行单元测试和真实模型测试；在真实 GNOME 会话手动验证录音、取消、粘贴、终端输出和服务重启恢复。
4. 检查许可证和第三方通知；二进制包还需收集实际分发的依赖与模型许可证。
5. 更新版本、CHANGELOG 和验证范围，再创建版本标签及 GitHub Release。

GitHub Actions 配置只运行不依赖本机模型的测试，5 项真实模型测试会跳过。桌面授权、焦点、麦克风和休眠唤醒需要实机验证；远程 CI 尚未运行。

## 分发路线

当前从源码目录运行，普通 `pip install` 的 wheel 并非完整桌面产品。安装包开发时需处理模型路径、首次下载、校验、系统依赖、桌面权限、更新与卸载；默认保留本地离线识别能力。
