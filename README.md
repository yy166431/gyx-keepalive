# gyx-keepalive

后台保活 dylib，供 **TrollFools 外部注入**到巨魔安装的原版 App，解决进后台
被 iOS 挂起导致注入的业务逻辑（流量重定向/定时器）停摆、"掉后台掉效果"。

## 用法（推荐路线：原版巨魔装 + TrollFools 注入）

1. 用巨魔(TrollStore)正常安装**原版 IPA**（能装、能打开）。
2. GitHub Actions 跑完，从 `KeepAlive-dylib` 工件下载 `KeepAlive.dylib`。
3. 打开 **TrollFools** → 选中该 App → 注入插件 → 选 `KeepAlive.dylib` → 应用。
4. 重开 App 即生效。测试：进 App 停留几秒 → 回桌面/锁屏 → 数分钟后看效果还在不在。

## 原理

- dylib 被注入后，constructor 自启，运行时激活 AVAudioSession(playback+
  mixWithOthers) 并循环播放**代码内构造的静音 PCM**（无外部文件、不改 Info.plist）。
- 巨魔 App 经 TrollFools 重签带高权限，运行时激活音频通常无需 plist 的 audio 声明。
- 进后台 beginBackgroundTask 续期 + 10s 守护定时器确保音频不断。
- 监听中断/路由/前后台切换自动恢复。

**非 100%**：iOS 硬限制，锁屏久/内存告急仍可能被回收；mixWithOthers 已尽量不吵别的 App。

## 文件

- `src/KeepAlive.m` — 保活 dylib 源码（纯注入自洽，无外部依赖）。
- `.github/workflows/build.yml` — macOS runner 编 arm64 + ldid 签名，产出 `KeepAlive.dylib`。
- `tools/` — 早期重打包方案的脚本（insert_dylib/repack/make_silence），当前路线用不到，保留备用。
