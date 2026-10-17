# 从独立 FFmpeg 源码构建 iOS 底层库

此入口只构建 FFmpeg n8.1、FFmpegKit 兼容层、libass 与配套依赖。输出包含真机 arm64、模拟器 arm64/x86_64 的静态 XCFramework 和依赖许可证。最低 iOS 16。KSPlayer 与 DisplayCriteria 由 App 从源码编译，不进入此 Release。

三个组件统一使用 `KleinerSource` 命名空间，`main` 与 `senplayer-ffmpeg81` 分支均已推送。锁清单中的增强提交位于 `KleinerSource/FFmpeg` 镜像仓库（上游仍记录为官方 FFmpeg）。`mobile_app` 的 Pod/协议/诊断适配和 `omm_app` 的原生构建工作流已经接入这些固定制品。

## 上传到 GitHub

三个仓库的目标地址统一为：`KleinerSource/KSPlayer`、`KleinerSource/FFmpeg`、`KleinerSource/FFmpegKit`。当前分支名保留为 `senplayer-ffmpeg81`；上传时可直接推送该分支，或在你的 GitHub 仓库中将它设为默认分支。

## 在 macOS 构建

同级目录应为 `FFmpegKit/`、`FFmpeg/`，不要求检出 KSPlayer。构建器优先读取同级 Git 仓库的固定对象，忽略未提交内容；缺对象时尝试锁清单中的远端，失败立即中止。FFmpegKit 的 helper、构建器和 podspec 来自当前仓库。

```bash
cd FFmpegKit
brew install autoconf automake libtool pkg-config cmake meson ninja nasm texinfo
export PATH="$(brew --prefix texinfo)/bin:$PATH"
python3 --version # 需要 Python 3.12 或更新版本
python3 iOSNative/build.py plan
python3 iOSNative/build.py prepare
python3 iOSNative/build.py build --jobs 3
python3 iOSNative/distribute.py build
python3 -m unittest discover -s iOSNative -p 'test_*.py' -v
```

默认输出为 `FFmpegKit/build/ios-native`，可用 `--output` 指定，例如应用的 `build/ios-native`。`--components-dir` 指定 FFmpeg 源码仓库的父目录。每次 `build` 都重新导出固定源码、helper、Podspec 和许可证，再检查原生库缓存。缓存键包含固定 C 库源码、构建器和 Xcode/SDK/构建工具版本；单独更新 helper 或 Podspec 会复用底层库。GMP 等主机辅助程序使用 macOS clang/SDK，目标代码使用 iOS clang/SDK；不会让主机程序继承 iOS 部署目标。

构建固定 dav1d 1.4.3、SRT 1.5.3、libass 0.17.1、FreeType、FriBidi、HarfBuzz、GMP、Nettle、GnuTLS。FFmpeg 配置强制检查 VideoToolbox、dav1d、SRT、RTSP、RTMP、GnuTLS 等能力，缺项即失败。此入口不构建 libmpv 或 libplacebo。

## 使用本地 CocoaPods

完成 `build` 后，在应用 Podfile 中使用底层库输出目录，并从独立 KSPlayer 仓库获取播放器源码。下面的源码路径按实际位置调整。

在 FFmpegKit 目录执行 `export IOS_NATIVE_PODS="$(pwd)/build/ios-native/pods"`，再从同一 shell 进入应用执行 `pod install`。

```ruby
native = ENV.fetch('IOS_NATIVE_PODS') # 指向构建输出的绝对 pods 路径
pod 'KSPlayer', :path => '/absolute/path/to/KSPlayer'
pod 'DisplayCriteria', :path => '/absolute/path/to/KSPlayer'
pod 'FFmpegKit', :path => "#{native}/FFmpegKit"
pod 'Libass', :path => "#{native}/FFmpegKit"
```

`omm_app` 自动把底层 Release 安装到 `IOS_NATIVE_PODS/FFmpegKit`，把最新 KSPlayer `main` 源码检出到 `IOS_NATIVE_PODS/KSPlayer`；应用现有 Pod 配置会编译 KSPlayer、DisplayCriteria 及 Metal 资源。已有 `media_kit` 的应用仍需检查最终 FFmpeg 链接。

## 跨仓库复用 Release

`FFmpegKit` 的 iOS Release 只包含 FFmpegKit、Libass 和底层依赖的静态 XCFramework。固定标签为 `ios-native-latest`，每次成功发布覆盖同名附件，再清理其它 Release；旧 Git 标签保留。发布工作流串行执行，构建、测试或上传失败时不会清理历史 Release。

清单记录底层组件提交、构建指纹和 libav ABI。`omm_app` 下载归档、SHA-256 和清单并校验完整性，再独立拉取 KSPlayer 源码。App 缓存同时包含底层包 SHA-256 与 KSPlayer 提交号，确保任一更新都会重新构建对应产物。公开下载和源码检出不要求 `COMPONENTS_TOKEN`。

迁移时 App 也能复用现有组合 Release：只提取其中的 FFmpegKit 底层目录，忽略旧 KSPlayer 二进制。以后更新 KSPlayer 后，将修改推送到 KSPlayer 的 `main` 并重新运行 App 构建即可，无需重新发布 FFmpegKit。只有底层库或 FFmpegKit 兼容层变化时才更新此 Release。

FFmpegKit 的 Actions cache 用于底层库构建加速；KSPlayer 源码与构建缓存由 App 管理。

Swift Package 使用组件根目录的 `Package.swift`；KSPlayer 直接依赖同级 FFmpegKit，后者只引用本次构建的 XCFramework。Swift Package 固定采用默认输出目录，使用自定义输出时通过 CocoaPods 接入。

## 在 macOS 验证组件

```bash
cd ../KSPlayer
xcodebuild -scheme KSPlayer -destination 'generic/platform=iOS' -configuration Release build CODE_SIGNING_ALLOWED=NO
xcrun simctl list devices available
# 选择上一个命令列出的已安装 iPhone 模拟器 UDID
read -r simulator_id
xcodebuild -scheme KSPlayer -destination "platform=iOS Simulator,id=$simulator_id" test CODE_SIGNING_ALLOWED=NO
```

FFmpegKit 的 `iOS native component Release` 工作流在构建器相关文件更新到 `main` 时自动构建并更新底层库 Release，也支持手动触发，保留底层库真机/模拟器构建与 CocoaPods 校验。KSPlayer 的源码编译属于 App 构建；上面的 XCTest 和下方 CPU 色彩参考测试需针对单独检出的 KSPlayer 源码运行。

Windows 已执行构建器和色彩参考测试；不能执行 Apple SDK 编译、XCTest、GPU 或 HDR 真机验收。色彩测试可直接针对组件工作树运行：

```powershell
$env:KSPLAYER_SOURCE = (Resolve-Path ../KSPlayer).Path
$env:FFMPEG_SOURCE = (Resolve-Path ../FFmpeg).Path
python -m unittest discover -s iOSNative/tests -p test_color_reference.py -v
```

能力和未完成验收项见 [能力对照报告](2026-10-15_ios-components-report.md)，组件 API 见 KSPlayer 的 `Documents/iOS-FFmpeg81.md`。
