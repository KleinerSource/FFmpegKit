// swift-tools-version:5.9
import PackageDescription

let modules = [
    "Libavcodec", "Libavdevice", "Libavfilter", "Libavformat", "Libavutil", "Libswresample", "Libswscale",
    "libdav1d", "libsrt", "gmp", "nettle", "hogweed", "gnutls",
    "libfreetype", "libfribidi", "libharfbuzz", "libass",
]
let package = Package(
    name: "FFmpegKit",
    platforms: [.iOS(.v16)],
    products: [.library(name: "FFmpegKit", targets: ["FFmpegKit"])] + modules.map {
        .library(name: $0, targets: [$0])
    },
    targets: [
        .target(name: "FFmpegKit", dependencies: modules.map { .target(name: $0) },
                linkerSettings: [
                    .linkedFramework("AudioToolbox"), .linkedFramework("AVFoundation"),
                    .linkedFramework("CoreMedia"), .linkedFramework("CoreVideo"),
                    .linkedFramework("VideoToolbox"), .linkedFramework("Security"),
                    .linkedFramework("CoreText"), .linkedFramework("CoreGraphics"),
                    .linkedLibrary("bz2"), .linkedLibrary("c++"), .linkedLibrary("iconv"),
                    .linkedLibrary("resolv"), .linkedLibrary("z"),
                ]),
    ] + modules.map {
        .binaryTarget(name: $0, path: "build/ios-native/pods/FFmpegKit/Frameworks/\($0).xcframework")
    }
)
