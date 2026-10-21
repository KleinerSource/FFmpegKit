#!/usr/bin/env python3
"""Build reusable FFmpegKit and libass XCFramework Pods without player sources."""

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
    native_pkg = work / "packages/Native"
    native_sources = native_pkg / "Sources/FFmpegKit"
    native_sources.parent.mkdir(parents=True)
    shutil.copytree(native.ROOT / "Sources/FFmpegKit", native_sources)
    copy_frameworks(native.WORK / "pods/FFmpegKit/Frameworks", CLANG_MODULES,
                    native_pkg / "Frameworks")
    native_targets = ",\n    ".join(
        f'.binaryTarget(name: "{name}", path: "Frameworks/{name}.xcframework")'
        for name in CLANG_MODULES
    )
    native_dependencies = ", ".join(f'.target(name: "{name}")' for name in CLANG_MODULES)
    (native_pkg / "Package.swift").write_text(f'''// swift-tools-version:5.9
import PackageDescription
let package = Package(
  name: "FFmpegKit",
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
''', encoding="utf-8")
    return work


def static_framework(name, library, platform, headers):
    """Wrap a Clang static archive and its public headers in a framework."""
    framework = native.WORK / "distribution/frameworks" / platform / f"{name}.framework"
    if framework.exists():
        shutil.rmtree(framework)
    framework.mkdir(parents=True)
    shutil.copyfile(library, framework / name)
    if headers:
        header_root = framework / "Headers"
        header_root.mkdir()
        for header in headers:
            shutil.copyfile(header, header_root / header.name)
        modules = framework / "Modules"
        modules.mkdir()
        umbrella = header_root / f"{name}.h"
        umbrella.write_text("".join(f'#import "{header.name}"\n' for header in headers), encoding="utf-8")
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


def static_library(name, products, package):
    library = products / f"lib{name}.a"
    if library.is_file():
        return library
    object_file = products / f"{name}.o"
    if object_file.is_file():
        run(("libtool", "-static", "-o", library, object_file), package)
    if not library.is_file():
        raise RuntimeError(f"{name} 静态库没有生成: {library}")
    return library


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
        library = static_library(name, products, package)
        copied_headers = [Path(path) for path in header_paths]
        framework = static_framework(name, library, platform, copied_headers)
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
    ffmpeg = pods / "FFmpegKit"
    (ffmpeg / "FFmpegKit.podspec").write_text('''frameworks = File.expand_path('Frameworks', __dir__)
force_load = lambda do |slice|
  # Keep dav1d's public struct ABI with the FFmpeg build, not media_kit's dylib.
  %w[Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale libdav1d].map { |name|
    '"-Wl,-force_load,' + frameworks + '/' + name + '.xcframework/' + slice + '/' + name + '.framework/' + name + '"'
  }.join(' ')
end

Pod::Spec.new do |s|
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
  # Reference installed slices rather than files copied later during the build.
  s.user_target_xcconfig = {
    'OTHER_LDFLAGS' => '$(inherited) $(FFMPEGKIT_FORCE_LOAD_FLAGS)',
    'FFMPEGKIT_FORCE_LOAD_FLAGS[sdk=iphoneos*]' => force_load.call('ios-arm64'),
    'FFMPEGKIT_FORCE_LOAD_FLAGS[sdk=iphonesimulator*]' => force_load.call('ios-arm64_x86_64-simulator')
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
    ffmpeg = output / "FFmpegKit"
    ffmpeg.mkdir(parents=True, exist_ok=True)
    shutil.copytree(frameworks["FFmpegKit"], ffmpeg / "Frameworks/FFmpegKit.xcframework")

    (ffmpeg / "Frameworks").mkdir(exist_ok=True)
    copy_frameworks(pods / "FFmpegKit/Frameworks", native.LIBRARIES, ffmpeg / "Frameworks")
    shutil.copytree(pods / "FFmpegKit/Licenses", ffmpeg / "Licenses", dirs_exist_ok=True)
    shutil.copyfile(pods / "FFmpegKit/LICENSE", ffmpeg / "LICENSE")
    shutil.copyfile(pods / "FFmpegKit/native-versions.json", ffmpeg / "native-versions.json")
    podspecs(output)

    identity = native.release_key()
    versions = json.loads((ffmpeg / "native-versions.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": 2,
        "tag": identity["tag"],
        "fingerprint": identity,
        "sources": versions["components"],
        "ffmpeg_version": versions["ffmpeg_version"],
        "libav_versions": versions["libav_versions"],
        "frameworks": ["FFmpegKit", "Libass"],
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
    if not native_headers:
        raise RuntimeError("FFmpegKit 缺少公开头文件")
    run(("swift", "package", "dump-package"), package_root / "packages/Native")
    frameworks = {
        "FFmpegKit": build_xcframework("FFmpegKit", package_root / "packages/Native", native_headers),
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
