# iOS 播放组件源码升级交付记录

本次将增强直接落在独立 KSPlayer、FFmpeg、FFmpegKit 源码仓库，替代应用内构建补丁方案。三个仓库都使用 `KleinerSource` 命名空间，`main` 与 `senplayer-ffmpeg81` 已推送。`mobile_app` 与 `omm_app` 已接入固定组件：前者负责播放器路由与 Pod 配置，后者负责原生源码构建和发布缓存。Apple 编译、GPU、协议服务器和真机验收未执行。

## 源码基线与提交

| 组件 | 上游基线 | 当前源码提交 |
| --- | --- | --- |
| FFmpeg | 官方 n8.1 `9047fa1b084f76b1b4d065af2d743df1b40dfb56` | `904720ead5e74e4ef4ba1b79b64a2bd90fa6ab0b` |
| KSPlayer | 自有 fork `fb782b6f93bef9b68181a0d41e9dd22ef53d715b` | `99094fa6612de116ecff3c0c409090f99e2db1a5` |
| FFmpegKit | 构建器快照 `b80cde8f476bbc374310e9a34e2cf17104705030` | `KleinerSource/FFmpegKit` 当前分支 HEAD（以 `git rev-parse HEAD` 为准） |

KSPlayer 的能力提交为 API/依赖适配 `d6edbef`、显示/字幕 `18d4e86`、协议/恢复 `52285f3`；提交 `88f1a64` 还统一了 CocoaPods 模块，与 SwiftPM 的单模块布局一致。协议恢复提交依赖显示提交提供的输出能力和字幕清理接口，因此显示代码先提交。FFmpeg 的 Metal 分配改动和 FFmpegKit 的构建入口分别保留在各自仓库。

锁清单固定增强提交与公开依赖对象；构建器从这些 Git 对象导出源码，不采用 KSPlayer/FFmpeg 未提交修改。FFmpegKit 的 helper、podspec、Swift Package 和构建器直接维护在自身仓库，缓存键包含其文件内容及工具链。未发布的增强提交需要从同级仓库读取；仅克隆官方 FFmpeg 无法取得自有增强提交。

## 版本与 SenPlayer 对照

| 项目 | 本次源码/配置 | SenPlayer 已有证据 | 当前验证状态 |
| --- | --- | --- | --- |
| FFmpeg | n8.1 | 主 Mach-O 中 n8.1 | 固定源码导出与 VERSION 文件已核对，运行时待验证 |
| libavcodec | 62.28.100 | 62.28.100 | 导出头文件一致，运行时待验证 |
| libavformat / libavutil | 62.12.100 / 60.26.100 | 此记录没有独立版本取证 | 已锁定、待运行时对照 |
| libavdevice / libavfilter | 62.3.100 / 11.14.100 | 此记录没有独立版本取证 | 已锁定、待运行时对照 |
| libswresample / libswscale | 6.3.100 / 9.5.100 | 此记录没有独立版本取证 | 已锁定、待运行时对照 |
| KSPlayer 精确版本 | 保留自有 fork 基线定制 | 精确提交未知 | 不宣称完整复刻 |
| SRT / RTSP / RTMP | 编译配置启用；URL 直达 KSMEPlayer | 编译包含这些协议 | 源码实现，真实服务待测 |
| HDR10 / HLG / EDR | 系统路径保留，帧元数据补齐 | 已有静态证据 | 色彩 CPU 测试通过，真机待测 |
| Dolby Vision 5/8 | RPU 按帧复制、reshape、Metal 色彩变换 | 存在 DoviDisplayModel/RPU 管线 | 参考数学测试通过，完整片源/GPU 待测 |
| Dolby Vision 7 | 基础层；不重建 FEL | 完整范围未知 | 真实片源待测 |
| ASS / PGS | 复用文本路径；新增原生图形覆盖层、选择、关闭、延迟和清理 | 不在本次独立取证范围 | 源码已接通，设备待测 |

## 证据、结论与执行路径

| 证据 | 支持的结论 | 对应执行路径 |
| --- | --- | --- |
| 锁清单、独立 Git 提交、成功的 `build.py prepare` | 固定对象导出、版本检查、归档哈希校验完成；没有运行时源码补丁 | `fetch → git archive → prepare → build_slice → XCFramework` |
| `test_build.py` 的 9 项测试 | 路径/哈希校验、固定提交导出、交叉编译隔离、三架构 HarfBuzz 参数、缓存范围、缓存命中源码刷新及平台专用头文件排除有效 | 独立构建器单元验证 |
| 同源 Metal 数学的 11 项 CPU 测试，包含 FFmpeg FATE dv84 数据 | PQ、HLG、reshape、MMR 和 BT.2390 的受测计算符合参考值 | Metal 函数提取 → C++ 适配 → 参考向量比较 |
| KSPlayer 新增 `PlaybackRecoveryTest.swift` | 恢复预算、分类、默认协议配置、RPU 复制和旧回调应有的行为已定义 | macOS 模拟器 XCTest，当前未执行 |
| UTF-8、Swift 结构解析、Git diff 检查 | 受检源码编码与语法结构通过 | 不构成 Swift 类型检查或 Apple 链接验证 |

网络错误与硬解终止错误使用独立预算。连接 5 秒、读取 10 秒，网络最多 1/2/4 秒三次重试；恢复不重置预算。暂停期间不自动转为播放，直播不保存或恢复文件位置。停止、换片和释放取消待重试任务，来源实例和会话代次过滤旧回调。文本与图形字幕延迟共用轨道 delay；图形覆盖层不进入系统画中画。切换到 Metal 时停止系统画中画并通知能力变化。

## 尚未完成的外部验收

应用 Podfile、原生配置生成器、Dart 动态能力/调试信息及 `omm_app` 发布工作流已经恢复并提交。组件提供 `runtimeInfo`、`isLive`、`seekable`、`subtitleDelay` 和内嵌字幕选择接口，应用通过本地制品消费这些 API。尚未完成的是播放器的 Apple 编译和 XCTest、真实协议服务、GPU/HDR 输出及 iOS 真机回归。

2026-10-16 的 [Actions 38842658260](https://github.com/KleinerSource/FFmpegKit/actions/runs/38842658260) 已通过 FFmpeg n8.1 和配套依赖的真机 arm64、模拟器 arm64/x86_64 编译、XCFramework 打包及 11 项色彩参考测试。HarfBuzz 的交叉编译参数丢失和源码诊断升级为错误已修复。该运行随后在 CocoaPods 依赖解析阶段因找不到本地 DisplayCriteria 失败；`43caeae` 已将 `--include-podspecs` 改为官方支持的 glob，待后续运行验证。运行时版本与 Swift/链接验证仍不能由 C 库编译结果替代。

`4b08bc9` 在原生构建成功后立即保存缓存，`36f577f` 确保缓存命中前刷新固定源码，`71b1a78` 将原生缓存限定为 C 库源码、构建器及工具链。播放器、helper、Podspec 和许可证每次重新导出，单独调整这些输入不再使 FFmpeg 库缓存失效。应用接入已同步推送到 `mobile_app` 的 `f777fac0` 和 `omm_app` 的 `04581a1`（当前固定 `4b08bc9`，最终版本待同步）；配置生成器 6 项回归通过。

[Actions 38844956923](https://github.com/KleinerSource/FFmpegKit/actions/runs/38844956923) 已确认本地 Pods 解析成功，三架构原生缓存已保存。后续 Xcode 因逐文件复制的许可证同名而失败；`71b1a78` 将许可证作为目录资源保留层级，待后续编译验证。

[Actions 38847265777](https://github.com/KleinerSource/FFmpegKit/actions/runs/38847265777) 确认三架构 FFmpeg/依赖编译、XCFramework 打包、色彩参考检查及 Pods 解析成功；Swift 编译随后失败。锁定的 KSPlayer `5730815` 把互相引用的 Swift 文件拆到多个 CocoaPods 子规格，造成跨规格类型不可见；FFmpeg umbrella module 也暴露了 iOS 不支持的 AMF/CUDA 头文件。KSPlayer `88f1a648` 已改为单一 CocoaPods 模块；FFmpegKit 更新 KSPlayer 锁定提交，并在 module map 中排除这些平台专用头文件。缓存键现包括 framework/module map 生成规则，确保不复用带旧 module map 的 XCFramework。

[Actions 38851410085](https://github.com/KleinerSource/FFmpegKit/actions/runs/38851410085) 再次确认三架构原生库、XCFramework 和 Pods 解析通过；KSPlayer 编译暴露 FFmpegKit 8.1 的 Swift 类型差异。`99094fa` 修正 AVMediaType 歧义、SwsContext/缩放标志类型、HDR 数值类型和 MainActor 回调，并已锁入构建清单。该修订的 macOS 类型检查仍待 Actions 验证。

macOS 需执行新工作流或 [构建说明](README.md) 中的命令，验证三架构 XCFramework、CocoaPods 编译、Swift Package 模拟器 XCTest 和真机架构 Release 编译。包含 media_kit 的最终应用仍需完整链接，检查 FFmpeg/libmpv 共存、画中画、AirPlay 和播放路由。`omm_app` 工作流仍需手动触发，当前没有本机 Apple SDK 验证结果。

设备验收还包括原故障片约 5:25、正常 H.264/HEVC/AV1、硬软解、seek/倍速/暂停、有限恢复、认证/超时/中断/取消、HDR10/HLG、Dolby 5/8/7 基础层、SDR、ASS/PGS。当前没有以上设备验收结果；不能由历史应用方案的 Dart 测试数量代替。
