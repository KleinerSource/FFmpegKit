# 从独立组件源码构建 iOS 播放内核

此入口构建 FFmpeg n8.1、KSPlayer 与配套依赖，直接使用组件源码提交。输出包含真机 arm64、模拟器 arm64/x86_64、保持原名的 Swift 模块、KSPlayer Metal 资源和依赖许可证。最低 iOS 16。

三个组件统一使用 `KleinerSource` 命名空间，`main` 与 `senplayer-ffmpeg81` 分支均已推送。锁清单中的增强提交位于 `KleinerSource/FFmpeg` 镜像仓库（上游仍记录为官方 FFmpeg）。`mobile_app` 的 Pod/协议/诊断适配和 `omm_app` 的原生构建工作流已经接入这些固定制品。

## 上传到 GitHub

三个仓库的目标地址统一为：`KleinerSource/KSPlayer`、`KleinerSource/FFmpeg`、`KleinerSource/FFmpegKit`。当前分支名保留为 `senplayer-ffmpeg81`；上传时可直接推送该分支，或在你的 GitHub 仓库中将它设为默认分支。

## 在 macOS 构建

同级目录应为 `FFmpegKit/`、`KSPlayer/`、`FFmpeg/`。构建器优先读取同级 Git 仓库的固定对象，忽略未提交内容；缺对象时尝试锁清单中的远端，失败立即中止。FFmpegKit 的 helper、构建器和 podspec 来自当前仓库。

```bash
cd FFmpegKit
brew install autoconf automake libtool pkg-config cmake meson ninja nasm texinfo
export PATH="$(brew --prefix texinfo)/bin:$PATH"
python3 --version # 需要 Python 3.12 或更新版本
python3 iOSNative/build.py plan
python3 iOSNative/build.py prepare
python3 iOSNative/build.py build --jobs 3
python3 -m unittest discover -s iOSNative -p test_build.py -v
python3 -m unittest discover -s iOSNative/tests -p test_color_reference.py -v
```

默认输出为 `FFmpegKit/build/ios-native`，可用 `--output` 指定，例如应用的 `build/ios-native`。`--components-dir` 指定三个独立源码仓库的父目录。每次 `build` 都重新导出固定源码、helper、Podspec 和许可证，再检查原生库缓存。缓存键包含固定 C 库源码、构建器和 Xcode/SDK/构建工具版本；单独更新播放器或打包配置会复用原生库。清理输出后可重新构建。GMP 等主机辅助程序使用 macOS clang/SDK，目标代码使用 iOS clang/SDK；不会让主机程序继承 iOS 部署目标。

构建固定 dav1d 1.4.3、SRT 1.5.3、libass 0.17.1、FreeType、FriBidi、HarfBuzz、GMP、Nettle、GnuTLS。FFmpeg 配置强制检查 VideoToolbox、dav1d、SRT、RTSP、RTMP、GnuTLS 等能力，缺项即失败。此入口不构建 libmpv 或 libplacebo。

## 使用本地 CocoaPods

完成 `build` 后，在应用 Podfile 中使用生成的源码和制品目录。下面是与默认同级目录布局对应的配置片段，路径以应用实际位置调整；配置生成器也须使用相同入口。

在 FFmpegKit 目录执行 `export IOS_NATIVE_PODS="$(pwd)/build/ios-native/pods"`，再从同一 shell 进入应用执行 `pod install`。

```ruby
native = ENV.fetch('IOS_NATIVE_PODS') # 指向构建输出的绝对 pods 路径
pod 'KSPlayer', :path => "#{native}/KSPlayer"
pod 'DisplayCriteria', :path => "#{native}/KSPlayer"
pod 'FFmpegKit', :path => "#{native}/FFmpegKit"
pod 'Libass', :path => "#{native}/FFmpegKit"
```

应用中旧的 FFmpegKit 6.1.4 或远端 KSPlayer 声明应由上述声明替换。`omm_app` 直接下载 `ios-native-latest` Release Pods，使用该制品记录的组件版本，不再解析各源码仓库的 `main`。已有 `media_kit` 的应用需要额外检查最终链接、画中画、AirPlay 和播放器路由。

## 跨仓库复用 Release

`FFmpegKit` 的 iOS Release 会把 FFmpegKit、Libass、KSPlayer、DisplayCriteria 的静态 XCFramework 和 KSPlayer Metal 源资源一起打包。固定标签为 `ios-native-latest`，每次成功发布都会覆盖同名附件，并清理仓库中的其它 Release，始终只保留一个最新 Release；旧 Git 标签保留。发布工作流串行执行，构建、测试或上传失败时不会清理历史 Release。

清单仍记录精确源码提交、构建指纹和 libav ABI。`omm_app` 直接下载固定标签下的归档、SHA-256 和清单，校验归档内外清单、组件来源和架构；不要求这些提交与远程 `main` 一致，也不会跨仓库触发构建。下载碰上覆盖发布时会重试；Release 缺失则明确提示先发布组件。公开制品不需要在 App 仓库配置 `COMPONENTS_TOKEN`。

首次迁移时先发布 FFmpegKit 的 `ios-native-latest`，再运行 App 构建。以后修改 KSPlayer 后，也需要在 FFmpegKit 仓库重新运行组件发布工作流，才能将修复装入 App；仅修改源码不会改变已发布二进制。

Actions cache 只留在制品生产工作流里，作为失败重跑和“只更新 KSPlayer”时的内部加速手段；FFmpeg 底层库的编译配方未变时可复用缓存，应用始终从 Release 获取完整制品。

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

FFmpegKit 的 `iOS native component Release` 工作流在构建器相关文件更新到 `main` 时自动构建并更新最新 Release，也支持手动触发。工作流保留 CocoaPods 编译检查、模拟器 XCTest 和真机架构构建；只有需要读取私有组件源码时，才需在 FFmpegKit 仓库配置 `COMPONENTS_TOKEN`。

Windows 已执行构建器和色彩参考测试；不能执行 Apple SDK 编译、XCTest、GPU 或 HDR 真机验收。色彩测试可直接针对组件工作树运行：

```powershell
$env:KSPLAYER_SOURCE = (Resolve-Path ../KSPlayer).Path
$env:FFMPEG_SOURCE = (Resolve-Path ../FFmpeg).Path
python -m unittest discover -s iOSNative/tests -p test_color_reference.py -v
```

能力和未完成验收项见 [能力对照报告](2026-10-15_ios-components-report.md)，组件 API 见 KSPlayer 的 `Documents/iOS-FFmpeg81.md`。
