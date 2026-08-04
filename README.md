# gyx-keepalive

给侧载/TrollStore 版 App 加「静音音频后台保活」，解决进后台被 iOS 挂起导致
注入 dylib（流量重定向 / 授权 / 定时器）停止工作、「掉后台掉效果」的问题。

## 原理

iOS 不给普通第三方 App 真正的后台执行权。可用的稳定手段是声明
`UIBackgroundModes=audio` 并在进程内持续播放一段无声音频，让系统以为在放音乐
而不挂起进程，从而让 App 内其他逻辑持续运行。

**非 100%**：锁屏久了、系统内存告急仍可能被回收。这是 iOS 的硬限制。

## 组成

- `src/KeepAlive.m` — 保活 dylib 源码：constructor 自启，AVAudioPlayer 无限循环
  播放静音，监听中断/路由变化自动恢复，进后台申请 backgroundTask。
- `tools/insert_dylib.py` — 纯 Python 向主程序追加 `LC_LOAD_DYLIB`（跨平台，无需 mac）。
- `tools/make_silence.py` — 生成静音 WAV。
- `tools/repack.py` — 解包 IPA → 放入 dylib+音频 → 注入 → 加后台模式 → 重打包。
- `.github/workflows/build.yml` — macOS runner 编译 arm64 dylib 并重打包，产出
  `out_keepalive.ipa` 工件。

## 用法

1. 原始 IPA 放在 `ipa/`（当前为 `ipa/source.ipa`）。
2. push 到 GitHub 触发 Actions，或手动 `workflow_dispatch`。
3. 下载 `keepalive-ipa` 工件里的 `out_keepalive.ipa`。
4. 用 TrollStore 安装（落地自动 fakesign + 注入 entitlements）。

## 说明

- 原有注入的业务 dylib（`刺客fuck.dylib`）保持不动，仅新增保活模块。
- 重打包会删除旧 `_CodeSignature`，由 TrollStore 重签。
