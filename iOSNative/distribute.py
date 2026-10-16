#!/usr/bin/env python3
"""Build reusable static XCFramework Pods from the current player sources."""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import zipfile

import build as native


CLANG_MODULES = (
    "Libavcodec", "Libavdevice", "Libavfilter", "Libavformat", "Libavutil",
    "Libswresample", "Libswscale", "libdav1d", "libsrt", "gmp", "nettle",
    "hogweed", "gnutls", "libfreetype", "libfribidi", "libharfbuzz", "libass",
)


def run(args, cwd):
    args = [str(value) for value in args]
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def reset(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def copy_frameworks(source, names, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        framework = source / f"{name}.xcframework"
        if not (framework / "Info.plist").is_file():
            raise RuntimeError(f"缺少原生 XCFramework: {framework}")
        shutil.copytree(framework, destination / framework.name)


def package_sources():
    work = native.WORK / "distribution"
    reset(work)
    packages = work / "packages"
    player_source = native.COMPONENTS / "KSPlayer/Sources"
    if not (player_source / "KSPlayer").is_dir() or not (player_source / "DisplayCriteria").is_dir():
        raise RuntimeError(f"KSPlayer/DisplayCriteria 源码未检出: {player_source}")

    native_pkg = packages / "Native"
    native_sources = native_pkg / "Sources/FFmpegKit"
    native_sources.parent.mkdir(parents=True)
    shutil.copytree(native.ROOT / "Sources/FFmpegKit", native_sources)
    copy_frameworks(native.WORK / "pods/FFmpegKit/Frameworks", CLANG_MODULES,
                    native_pkg / "Frameworks")

    display_pkg = packages / "DisplayCriteria"
    shutil.copytree(player_source / "DisplayCriteria", display_pkg / "Sources/DisplayCriteria")

    player_pkg = packages / "KSPlayer"
    shutil.copytree(player_source / "KSPlayer", player_pkg / "Sources/KSPlayer")

    native_targets = ",\n    ".join(
        f'.binaryTarget(name: "{name}", path: "Frameworks/{name}.xcframework")'
        for name in CLANG_MODULES
    )
    native_dependencies = ", ".join(f'.target(name: "{name}")' for name in CLANG_MODULES)
    manifests = {
        native_pkg: f'''// swift-tools-version:5.9
import PackageDescription
let package = Package(
  name: "Native",
  platforms: [.iOS(.v16)],
  products: [.library(name: "FFmpegKit", type: .static, targets: ["FFmpegKit"])],
  targets: [
    .target(name: "FFmpegKit", dependencies: [{native_dependencies}],
      path: "Sources/FFmpegKit", publicHeadersPath: "include",
      linkerSettings: [
        .linkedFramework("AudioToolbox"), .linkedFramework("AVFoundation"),
        .linkedFramework("CoreMedia"), .linkedFramework("CoreVideo"),
        .linkedFramework("VideoToolbox"), .linkedFramework("Security"),
        .linkedLibrary("bz2"), .linkedLibrary("c++"), .linkedLibrary("iconv"),
        .linkedLibrary("resolv"), .linkedLibrary("z")
      ])
  ] + [
    {native_targets}
  ]
)
''',
        display_pkg: '''// swift-tools-version:5.9
import PackageDescription
let package = Package(
  name: "DisplayCriteria",
  platforms: [.iOS(.v16)],
  products: [.library(name: "DisplayCriteria", type: .static, targets: ["DisplayCriteria"])],
  targets: [.target(name: "DisplayCriteria", path: "Sources/DisplayCriteria", publicHeadersPath: "include",
    linkerSettings: [.linkedFramework("AVFoundation")])]
)
''',
        player_pkg: '''// swift-tools-version:5.9
import PackageDescription
let package = Package(
  name: "KSPlayer",
  platforms: [.iOS(.v16)],
  dependencies: [
    .package(name: "FFmpegKit", path: "../Native"),
    .package(name: "DisplayCriteria", path: "../DisplayCriteria")
  ],
  products: [.library(name: "KSPlayer", type: .static, targets: ["KSPlayer"])],
  targets: [
    .target(name: "KSPlayer", dependencies: [
      .product(name: "FFmpegKit", package: "FFmpegKit"),
      .product(name: "DisplayCriteria", package: "DisplayCriteria")
    ], resources: [.process("Metal/Shaders.metal")],
      swiftSettings: [.enableExperimentalFeature("StrictConcurrency")])
  ]
)
''',
    }
    for directory, manifest in manifests.items():
        (directory / "Package.swift").write_text(manifest, encoding="utf-8")
    return work


def static_framework(name, library, module_directory, platform, headers=None):
    """Wrap a SwiftPM static archive and its module interface in a framework."""
    framework = native.WORK / "distribution/frameworks" / platform / f"{name}.framework"
    if framework.exists():
        shutil.rmtree(framework)
    framework.mkdir(parents=True)
    shutil.copyfile(library, framework / name)
    if module_directory is not None and module_directory.is_dir():
        destination = framework / "Modules" / f"{name}.swiftmodule"
        shutil.copytree(module_directory, destination)
        interfaces = list(destination.glob("*.swiftinterface"))
        if not interfaces:
            raise RuntimeError(f"{name} 缺少可分发 Swift module interface: {module_directory}")
    elif headers is not None:
        header_root = framework / "Headers"
        header_root.mkdir()
        for header in headers:
            shutil.copyfile(header, header_root / header.name)
        modules = framework / "Modules"
        modules.mkdir()
        umbrella = modules / f"{name}.h"
        umbrella.write_text("".join(f'#import "{header.name}"\n' for header in headers), encoding="utf-8")
        if name == "FFmpegKit":
            modulemap = f'framework module {name} {{\n  umbrella header "{name}.h"\n  export *\n}}\n'
        else:
            modulemap = f'framework module {name} {{\n  umbrella header "{name}.h"\n  export *\n}}\n'
        (modules / "module.modulemap").write_text(modulemap, encoding="utf-8")
    else:
        raise RuntimeError(f"{name} framework module 信息缺失")

    sdk_platform = "iPhoneOS" if platform == "iphoneos" else "iPhoneSimulator"
    info = {
        "CFBundleExecutable": name,
        "CFBundleIdentifier": f"org.kleinersource.{name.lower()}",
        "CFBundleName": name,
        "CFBundlePackageType": "FMWK",
        "CFBundleVersion": "1",
        "MinimumOSVersion": "16.0",
        "CFBundleSupportedPlatforms": [sdk_platform],
        "MachOType": "staticlib",
    }
    with (framework / "Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    return framework


def build_xcframework(name, package, header_paths=()):
    work = native.WORK / "distribution/archives" / name
    work.mkdir(parents=True, exist_ok=True)
    frameworks = []
    for sdk, platform, archs in (
        ("iphoneos", "iphoneos", "arm64"),
        ("iphonesimulator", "iphonesimulator", "arm64 x86_64"),
    ):
        derived = work / f"DerivedData-{sdk}"
        run(("xcodebuild", "build", "-scheme", name, "-configuration", "Release",
             "-destination", f"generic/platform={'iOS' if sdk == 'iphoneos' else 'iOS Simulator'}",
             "-derivedDataPath", derived, "BUILD_LIBRARY_FOR_DISTRIBUTION=YES",
             "CODE_SIGNING_ALLOWED=NO", "ONLY_ACTIVE_ARCH=NO", f"ARCHS={archs}"), package)
        products = derived / "Build/Products" / ("Release-iphoneos" if sdk == "iphoneos" else "Release-iphonesimulator")
        library = products / f"lib{name}.a"
        if not library.is_file():
            raise RuntimeError(f"{name} 静态库没有生成: {library}")
        module_dir = products / f"{name}.swiftmodule"
        copied_headers = [Path(path) for path in header_paths]
        framework = static_framework(name, library, module_dir if module_dir.is_dir() else None,
                                     platform, copied_headers or None)
        frameworks.append(framework)

    output = native.WORK / "distribution/frameworks" / (name + ".xcframework")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(("xcodebuild", "-create-xcframework", "-framework", frameworks[0],
         "-framework", frameworks[1], "-output", output), package)
    if not (output / "Info.plist").is_file():
        raise RuntimeError(f"{name} XCFramework 未生成")
    return output


def podspecs(pods):
    player = pods / "KSPlayer"
    ffmpeg = pods / "FFmpegKit"
    (player / "KSPlayer.podspec").write_text('''Pod::Spec.new do |s|
  s.name = 'KSPlayer'
  s.version = '1.2.0'
  s.summary = 'Prebuilt KSPlayer XCFramework'
  s.homepage = 'https://github.com/KleinerSource/KSPlayer'
  s.license = { :type => 'GPL-3.0', :file => 'Licenses/KSPlayer/LICENSE' }
  s.author = { 'KleinerSource' => 'https://github.com/KleinerSource' }
  s.source = { :git => 'https://github.com/KleinerSource/KSPlayer.git', :tag => s.version.to_s }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  s.vendored_frameworks = 'Frameworks/KSPlayer.xcframework'
  s.resource_bundles = { 'KSPlayer_KSPlayer' => ['Resources/Metal/Shaders.metal'] }
  s.dependency 'FFmpegKit', '8.1.0'
  s.dependency 'DisplayCriteria', '1.1.0'
  s.dependency 'Libass', '0.17.1'
  s.frameworks = 'AudioToolbox', 'AVFoundation', 'VideoToolbox', 'UIKit'
  s.weak_framework = 'MetalKit'
end
''', encoding="utf-8")
    (player / "DisplayCriteria.podspec").write_text('''Pod::Spec.new do |s|
  s.name = 'DisplayCriteria'
  s.version = '1.1.0'
  s.summary = 'Prebuilt DisplayCriteria XCFramework'
  s.homepage = 'https://github.com/KleinerSource/KSPlayer'
  s.license = 'MIT'
  s.author = { 'KleinerSource' => 'https://github.com/KleinerSource' }
  s.source = { :git => 'https://github.com/KleinerSource/KSPlayer.git', :tag => s.version.to_s }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  s.vendored_frameworks = 'Frameworks/DisplayCriteria.xcframework'
  s.frameworks = 'AVFoundation'
end
''', encoding="utf-8")
    (ffmpeg / "FFmpegKit.podspec").write_text('''Pod::Spec.new do |s|
  s.name = 'FFmpegKit'
  s.version = '8.1.0'
  s.summary = 'Prebuilt FFmpeg 8.1 and compatibility helpers'
  s.homepage = 'https://github.com/KleinerSource/FFmpegKit'
  s.license = { :type => 'GPL-3.0-or-later', :file => 'LICENSE' }
  s.author = { 'KleinerSource' => 'https://github.com/KleinerSource' }
  s.source = { :git => 'https://github.com/KleinerSource/FFmpegKit.git', :tag => 'n8.1' }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  s.vendored_frameworks = 'Frameworks/FFmpegKit.xcframework', 'Frameworks/Libav*.xcframework',
    'Frameworks/Libsw*.xcframework', 'Frameworks/libdav1d.xcframework', 'Frameworks/libsrt.xcframework',
    'Frameworks/gmp.xcframework', 'Frameworks/nettle.xcframework', 'Frameworks/hogweed.xcframework',
    'Frameworks/gnutls.xcframework'
  # Keep FFmpeg 8.1 definitions in the executable when media_kit is also linked.
  s.user_target_xcconfig = {
    'OTHER_LDFLAGS' => '$(inherited) ' + %w[Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale].map { |name|
      '-force_load "$(PODS_XCFRAMEWORKS_BUILD_DIR)/FFmpegKit/' + name + '.framework/' + name + '"'
    }.join(' ')
  }
  s.resource_bundles = { 'FFmpegKit_FFmpegKit' => ['Licenses', 'native-versions.json'] }
  s.frameworks = 'AudioToolbox', 'AVFoundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'Security'
  s.libraries = 'bz2', 'c++', 'iconv', 'resolv', 'z'
  s.dependency 'Libass', '0.17.1'
end
''', encoding="utf-8")
    shutil.copyfile(native.ROOT / "Libass.podspec", ffmpeg / "Libass.podspec")


def assemble(frameworks):
    output = native.WORK / "distribution/pods"
    pods = native.WORK / "pods"
    player = output / "KSPlayer"
    ffmpeg = output / "FFmpegKit"
    for folder in (player, ffmpeg):
        folder.mkdir(parents=True, exist_ok=True)
    (player / "Frameworks").mkdir(exist_ok=True)
    (player / "Resources/Metal").mkdir(parents=True, exist_ok=True)
    (player / "Licenses/KSPlayer").mkdir(parents=True, exist_ok=True)
    for name in ("KSPlayer", "DisplayCriteria"):
        shutil.copytree(frameworks[name], player / "Frameworks" / f"{name}.xcframework")
    shutil.copytree(frameworks["FFmpegKit"], ffmpeg / "Frameworks/FFmpegKit.xcframework")
    shader = native.COMPONENTS / "KSPlayer/Sources/KSPlayer/Metal/Shaders.metal"
    if not shader.is_file():
        raise RuntimeError(f"KSPlayer Metal shader 源码缺失: {shader}")
    shutil.copyfile(shader, player / "Resources/Metal/Shaders.metal")
    license_file = native.COMPONENTS / "KSPlayer/LICENSE"
    if not license_file.is_file():
        raise RuntimeError(f"KSPlayer LICENSE 缺失: {license_file}")
    shutil.copyfile(license_file, player / "Licenses/KSPlayer/LICENSE")

    (ffmpeg / "Frameworks").mkdir(exist_ok=True)
    copy_frameworks(pods / "FFmpegKit/Frameworks", native.LIBRARIES, ffmpeg / "Frameworks")
    shutil.copytree(pods / "FFmpegKit/Licenses", ffmpeg / "Licenses", dirs_exist_ok=True)
    shutil.copyfile(pods / "FFmpegKit/LICENSE", ffmpeg / "LICENSE")
    shutil.copyfile(pods / "FFmpegKit/native-versions.json", ffmpeg / "native-versions.json")
    podspecs(output)

    identity = native.release_key()
    versions = json.loads((ffmpeg / "native-versions.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": 1,
        "tag": identity["tag"],
        "fingerprint": identity,
        "sources": versions["components"],
        "ffmpeg_version": versions["ffmpeg_version"],
        "libav_versions": versions["libav_versions"],
        "frameworks": ["FFmpegKit", "Libass", "KSPlayer", "DisplayCriteria"],
    }
    manifest_path = native.WORK / "distribution/release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = native.WORK / "ios-native-pods.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                info = zipfile.ZipInfo(file.relative_to(output.parent).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, file.read_bytes())
        info = zipfile.ZipInfo("release-manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zipped.writestr(info, manifest_path.read_bytes())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (native.WORK / "ios-native-pods.zip.sha256").write_text(
        f"{checksum}  ios-native-pods.zip\n", encoding="ascii"
    )
    print(f"release_tag={identity['tag']}")
    print(f"archive={archive}")


def build():
    if native.platform.system() != "Darwin":
        raise RuntimeError("iOS XCFramework 构建需要 macOS 和 Xcode")
    package_root = package_sources()
    native_headers = list((native.ROOT / "Sources/FFmpegKit/include").glob("*.h"))
    display_headers = list((native.COMPONENTS / "KSPlayer/Sources/DisplayCriteria/include").glob("*.h"))
    if not native_headers or not display_headers:
        raise RuntimeError("FFmpegKit 或 DisplayCriteria 缺少公开头文件")
    for package in (package_root / "packages/Native", package_root / "packages/DisplayCriteria",
                    package_root / "packages/KSPlayer"):
        run(("swift", "package", "dump-package"), package)
    frameworks = {
        "FFmpegKit": build_xcframework("FFmpegKit", package_root / "packages/Native", native_headers),
        "DisplayCriteria": build_xcframework("DisplayCriteria", package_root / "packages/DisplayCriteria", display_headers),
        "KSPlayer": build_xcframework("KSPlayer", package_root / "packages/KSPlayer"),
    }
    assemble(frameworks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build",))
    parser.add_argument("--components-dir", type=Path, default=native.COMPONENTS)
    parser.add_argument("--output", type=Path, default=native.WORK)
    args = parser.parse_args()
    native.COMPONENTS = args.components_dir.resolve()
    native.WORK = args.output.resolve()
    build()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
