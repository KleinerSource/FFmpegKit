#!/usr/bin/env python3
"""构建固定源码的 iOS 播放依赖；下载、源码和所有产物均位于 build/ios-native。"""

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPONENTS = ROOT.parent
WORK = ROOT / "build/ios-native"
RELEASE_TAG = "ios-native-latest"
LOCK = json.loads((HERE / "sources.lock.json").read_text(encoding="utf-8"))
SLICES = (("iphoneos", "arm64"), ("iphonesimulator", "arm64"), ("iphonesimulator", "x86_64"))
ORDER = ("gmp", "nettle", "gnutls", "srt", "dav1d", "freetype", "fribidi", "harfbuzz", "libass", "FFmpeg")
# 模块名是 KSPlayer 的公开依赖；不把新的 libav 静态库混入 media_kit 的 libmpv。
LIBRARIES = {
    "Libavcodec": ("avcodec", "libavcodec"),
    "Libavdevice": ("avdevice", "libavdevice"),
    "Libavfilter": ("avfilter", "libavfilter"),
    "Libavformat": ("avformat", "libavformat"),
    "Libavutil": ("avutil", "libavutil"),
    "Libswresample": ("swresample", "libswresample"),
    "Libswscale": ("swscale", "libswscale"),
    "libdav1d": ("dav1d", "dav1d"), "libsrt": ("srt", "srt"),
    "gmp": ("gmp", None), "nettle": ("nettle", "nettle"),
    "hogweed": ("hogweed", "nettle"), "gnutls": ("gnutls", "gnutls"),
    "libfreetype": ("freetype", "freetype2"), "libfribidi": ("fribidi", "fribidi"),
    "libharfbuzz": ("harfbuzz", "harfbuzz"), "libass": ("ass", "ass"),
}


def run(args, *, cwd=None, env=None, capture=False):
    args = [str(arg) for arg in args]
    if not capture:
        print("+ " + shlex.join(args), flush=True)
    return subprocess.run(args, cwd=cwd, env=env, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          text=capture, encoding="utf-8" if capture else None)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_revision(path):
    if not (path / ".git").exists():
        return None
    return run(["git", "-C", path, "rev-parse", "HEAD"], capture=True).stdout.strip()


def extract(archive, destination):
    """拒绝越界链接和特殊文件，保留归档内的正常源码链接。"""
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.isdev():
                raise RuntimeError(f"不安全的归档路径: {member.name}")
            if member.issym() or member.islnk():
                link = ((target.parent if member.issym() else destination) / member.linkname).resolve()
                if not link.is_relative_to(destination.resolve()):
                    raise RuntimeError(f"不安全的归档链接: {member.name}")
        source.extractall(destination, filter="data")


def fetch(name):
    spec = LOCK["sources"][name]
    destination = WORK / name
    if "git" in spec:
        local = COMPONENTS / name
        if (local / ".git").exists():
            destination = local
        # 其余 Git 源继续由 sources.lock.json 固定，尤其保持 FFmpeg n8.1 ABI。
        if not destination.exists():
            run(["git", "clone", "--filter=blob:none", "--no-checkout", spec["git"], destination])
        if subprocess.run(["git", "-C", str(destination), "cat-file", "-e", spec["commit"] + "^{commit}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run(["git", "-C", destination, "fetch", spec["git"], spec["commit"]])
        run(["git", "-C", destination, "cat-file", "-e", spec["commit"] + "^{commit}"], capture=True)
        # 后续 export 从固定对象读取，不采用 checkout 的未提交内容。
        return destination
    archive = WORK / "downloads" / spec["url"].rsplit("/", 1)[-1]
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        temporary = archive.with_suffix(archive.suffix + ".download")
        with urllib.request.urlopen(spec["url"], timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    if sha256(archive) != spec["sha256"]:
        raise RuntimeError(f"{name} 源码 SHA-256 校验失败: {archive}")
    stamp = destination / ".source-sha256"
    if not stamp.exists() or stamp.read_text() != spec["sha256"]:
        if destination.exists():
            shutil.rmtree(destination)
        staging = WORK / (name + "-extract")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        extract(archive, staging)
        children = list(staging.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise RuntimeError(f"{name} 源码归档必须只有一个根目录")
        children[0].rename(destination)
        staging.rmdir()
        stamp.write_text(spec["sha256"], encoding="utf-8")
    return destination


def export_git(name, destination):
    source = fetch(name)
    archive = WORK / (name + "-source.tar")
    revision = LOCK["sources"][name]["commit"]
    run(["git", "-C", source, "archive", "--format=tar", "-o", archive, revision])
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    extract(archive, destination)
    archive.unlink()


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    resolved_sources = {}
    for name in LOCK["sources"]:
        fetch(name)
        spec = LOCK["sources"][name]
        resolved_sources[name] = spec.get("commit", spec.get("sha256"))
    for name in ("FFmpeg", "dav1d", "srt"):
        export_git(name, WORK / "sources" / name)
    # version.sh 会向上发现应用仓库；显式版本文件保证运行时不混入应用 Git 版本。
    (WORK / "sources/FFmpeg/VERSION").write_text(LOCK["ffmpeg_version"] + "\n", encoding="utf-8")
    versions = {}
    for name, expected in LOCK["libav_versions"].items():
        headers = "\n".join(path.read_text(encoding="utf-8") for path in (WORK / "sources/FFmpeg" / name).glob("version*.h"))
        actual = ".".join(re.search(r"#define\s+" + name.upper() + r"_VERSION_" + part + r"\s+(\d+)", headers)[1]
                          for part in ("MAJOR", "MINOR", "MICRO"))
        if actual != expected:
            raise RuntimeError(f"{name} 版本不匹配: {actual}，预期 {expected}")
        versions[name] = actual
    pods = WORK / "pods"
    # helper 直接来自当前 FFmpegKit 源码；旧的预编译二进制不进入 Pods。
    kit = pods / "FFmpegKit"
    helpers = kit / "Sources/FFmpegKit"
    if helpers.exists():
        shutil.rmtree(helpers)
    shutil.copytree(ROOT / "Sources/FFmpegKit", helpers)
    for spec in ("FFmpegKit", "Libass"):
        shutil.copyfile(ROOT / f"{spec}.podspec", kit / f"{spec}.podspec")
    licenses = kit / "Licenses"
    if licenses.exists():
        shutil.rmtree(licenses)
    for name in LOCK["sources"]:
        source = WORK / "sources" / name if name in ("FFmpeg", "dav1d", "srt") else WORK / name
        target = licenses / name
        target.mkdir(parents=True)
        for pattern in ("COPYING*", "LICENSE*", "LICENCE*", "docs/FTL.TXT", "docs/GPLv2.TXT"):
            for file in source.glob(pattern):
                if file.is_file():
                    shutil.copyfile(file, target / file.name)
    shutil.copyfile(ROOT / "LICENSE", kit / "LICENSE")
    (licenses / "FFmpegKit").mkdir()
    shutil.copyfile(ROOT / "LICENSE", licenses / "FFmpegKit/LICENSE")
    ffmpegkit_revision = git_revision(ROOT)
    versions = {
        "ffmpeg_version": LOCK["ffmpeg_version"],
        "libav_versions": versions,
        "sources": resolved_sources,
        "components": {
            "FFmpegKit": ffmpegkit_revision,
            "Libass": ffmpegkit_revision,
        },
    }
    (kit / "native-versions.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")
    return pods


def cache_key():
    digest = hashlib.sha256()
    # 原生库可复用键只覆盖编译配方；helper、Podspec 和许可证由 prepare 刷新。
    digest.update(json.dumps(LOCK, sort_keys=True).encode("utf-8"))
    digest.update(json.dumps({"slices": SLICES, "order": ORDER, "libraries": LIBRARIES}, sort_keys=True).encode("utf-8"))
    for function in (run, slice_environment, meson_cross, build_slice, framework):
        digest.update(inspect.getsource(function).encode("utf-8"))
    digest.update(platform.machine().encode())
    if platform.system() == "Darwin":
        for args in (("xcodebuild", "-version"), ("xcrun", "--sdk", "iphoneos", "--show-sdk-version"),
                     ("xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"),
                     ("clang", "--version"), ("cmake", "--version"), ("meson", "--version"),
                     ("ninja", "--version"), ("nasm", "-v"), ("pkg-config", "--version"),
                     ("make", "--version"), ("autoconf", "--version"), ("automake", "--version"),
                     ("glibtool", "--version"), ("makeinfo", "--version"), (sys.executable, "--version")):
            digest.update(run(args, capture=True).stdout.encode())
    return digest.hexdigest()


def release_key():
    """固定滚动标签，清单仍记录组件提交和 Apple 工具链指纹。"""
    ffmpegkit = git_revision(ROOT)
    if not ffmpegkit:
        raise RuntimeError("Release 指纹需要已检出的 FFmpegKit Git 提交")
    identity = {
        "schema": 2,
        "ffmpegkit_revision": ffmpegkit,
        "native_cache_key": cache_key(),
    }
    identity["tag"] = RELEASE_TAG
    return identity


def slice_environment(sdk, arch, prefix):
    sdkroot = run(["xcrun", "--sdk", sdk, "--show-sdk-path"], capture=True).stdout.strip()
    target = f"{arch}-apple-ios{LOCK['ios_minimum']}" + ("-simulator" if sdk == "iphonesimulator" else "")
    clang = run(["xcrun", "--sdk", sdk, "--find", "clang"], capture=True).stdout.strip()
    host_sdkroot = run(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"], capture=True
    ).stdout.strip()
    host_clang = run(
        ["xcrun", "--sdk", "macosx", "--find", "clang"], capture=True
    ).stdout.strip()
    if not host_clang or not host_sdkroot:
        raise RuntimeError("无法定位构建机 macOS SDK 或 clang")
    host_cc = shlex.join([host_clang, "-isysroot", host_sdkroot])
    flags = ["-target", target, "-isysroot", sdkroot, "-O2", "-fPIC", "-I" + str(prefix / "include")]
    cxx_flags = flags + ["-stdlib=libc++"]
    env = os.environ.copy()
    env.update(CC=clang, CXX=clang + "++", OBJC=clang, AR="/usr/bin/ar", RANLIB="/usr/bin/ranlib",
               CFLAGS=shlex.join(flags), CXXFLAGS=shlex.join(cxx_flags), CPPFLAGS=shlex.join(flags),
               LDFLAGS=shlex.join(flags + ["-L" + str(prefix / "lib")]),
               # Autoconf 依赖（尤其 GMP）会在交叉编译时生成并运行构建机辅助程序。
               # 这些程序必须使用 macOS 编译器，不能继承 iOS 的 target/sysroot。
               CC_FOR_BUILD=host_cc, HOST_CC=host_cc, CPP_FOR_BUILD="",
               CFLAGS_FOR_BUILD="", CPPFLAGS_FOR_BUILD="", LDFLAGS_FOR_BUILD="",
               PKG_CONFIG_PATH="", PKG_CONFIG_LIBDIR=str(prefix / "lib/pkgconfig"),
               PKG_CONFIG_SYSROOT_DIR="", PKG_CONFIG="pkg-config", SDKROOT="",
               MACOSX_DEPLOYMENT_TARGET="", IPHONEOS_DEPLOYMENT_TARGET="")
    return env, flags, sdkroot


def meson_cross(path, env, flags, arch):
    quote = lambda value: "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
    args = ", ".join(map(quote, flags))
    cxx_args = ", ".join(map(quote, flags + ["-stdlib=libc++"]))
    text = f"""[binaries]
c = {quote(env['CC'])}
cpp = {quote(env['CXX'])}
objc = {quote(env['CC'])}
objcpp = {quote(env['CXX'])}
ar = '/usr/bin/ar'
strip = '/usr/bin/strip'
pkg-config = 'pkg-config'
[host_machine]
system = 'darwin'
cpu_family = '{'aarch64' if arch == 'arm64' else 'x86_64'}'
cpu = '{arch}'
endian = 'little'
[properties]
needs_exe_wrapper = true
[built-in options]
c_args = [{args}]
cpp_args = [{cxx_args}]
objc_args = [{args}]
objcpp_args = [{cxx_args}]
c_link_args = [{args}, '-L{path.parent / 'prefix/lib'}']
cpp_link_args = [{cxx_args}, '-L{path.parent / 'prefix/lib'}']
"""
    path.write_text(text, encoding="utf-8")


def build_slice(sdk, arch, jobs):
    work = WORK / "slices" / f"{sdk}-{arch}"
    prefix = work / "prefix"
    work.mkdir(parents=True, exist_ok=True)
    prefix.mkdir(exist_ok=True)
    env, flags, sdkroot = slice_environment(sdk, arch, prefix)
    cross = work / "cross.meson"
    meson_cross(cross, env, flags, arch)
    host = "aarch64-apple-darwin" if arch == "arm64" else "x86_64-apple-darwin"
    for name in ORDER:
        source = WORK / "sources" / name if name in ("FFmpeg", "dav1d", "srt") else WORK / name
        build = work / name
        if build.exists():
            shutil.rmtree(build)
        build.mkdir()
        if name in ("dav1d", "freetype", "fribidi", "harfbuzz"):
            options = {
                "dav1d": ["-Denable_tools=false", "-Denable_tests=false", "-Denable_examples=false"],
                "freetype": ["-Dbrotli=disabled", "-Dharfbuzz=disabled", "-Dpng=disabled", "-Dbzip2=disabled"],
                "fribidi": ["-Ddeprecated=false", "-Ddocs=false", "-Dtests=false"],
                "harfbuzz": ["-Dglib=disabled", "-Dgobject=disabled", "-Dicu=disabled", "-Ddocs=disabled", "-Dtests=disabled",
                             "-Dcairo=disabled", "-Dchafa=disabled", "-Dintrospection=disabled", "-Dfreetype=enabled",
                             # 5.3.1 的源码 pragma 会把新版 Clang 警告升级为错误；使用其官方开关。
                             # Meson 的命令行数组会替换 cross file，必须保留完整目标参数。
                             f"-Dc_args={env['CFLAGS']} -DHB_NO_PRAGMA_GCC_DIAGNOSTIC_ERROR",
                             f"-Dcpp_args={env['CXXFLAGS']} -DHB_NO_PRAGMA_GCC_DIAGNOSTIC_ERROR"],
            }[name]
            run(["meson", "setup", build, source, "--cross-file", cross, "--prefix", prefix,
                 "--libdir=lib", "--default-library=static", "--buildtype=release", "--wrap-mode=nodownload", *options], env=env)
            run(["meson", "compile", "-C", build, "-j", jobs], env=env)
            run(["meson", "install", "-C", build], env=env)
        elif name == "srt":
            run(["cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_SYSTEM_NAME=iOS",
                 f"-DCMAKE_OSX_SYSROOT={sdkroot}", f"-DCMAKE_OSX_ARCHITECTURES={arch}",
                 f"-DCMAKE_OSX_DEPLOYMENT_TARGET={LOCK['ios_minimum']}", f"-DCMAKE_INSTALL_PREFIX={prefix}",
                 f"-DCMAKE_PREFIX_PATH={prefix}", f"-DCMAKE_C_COMPILER={env['CC']}", f"-DCMAKE_CXX_COMPILER={env['CXX']}",
                 "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON", "-DENABLE_STATIC=ON",
                 "-DENABLE_SHARED=OFF", "-DENABLE_APPS=OFF", "-DENABLE_TESTING=OFF", "-DUSE_ENCLIB=gnutls"], env=env)
            run(["cmake", "--build", build, "--parallel", jobs], env=env)
            run(["cmake", "--install", build], env=env)
        elif name == "FFmpeg":
            options = [f"--prefix={prefix}", "--target-os=darwin", "--enable-cross-compile", f"--arch={arch}",
                       f"--cc={env['CC']}", f"--cxx={env['CXX']}", f"--sysroot={sdkroot}",
                       f"--extra-cflags={env['CFLAGS']}", f"--extra-ldflags={env['LDFLAGS']} -lc++",
                       "--pkg-config=pkg-config", "--pkg-config-flags=--static", "--enable-static", "--disable-shared",
                       "--disable-programs", "--disable-doc", "--disable-debug", "--disable-autodetect", "--enable-pic",
                       "--enable-gpl", "--enable-version3", "--enable-network", "--enable-videotoolbox", "--enable-audiotoolbox",
                       "--enable-gnutls", "--enable-libsrt", "--enable-libdav1d", "--enable-libass", "--enable-zlib",
                       "--enable-bzlib", "--enable-iconv", "--enable-protocol=rtmp,rtmps,tcp,udp,rtp,libsrt,http,https,tls",
                       "--enable-demuxer=rtsp,rtp", "--enable-decoder=libdav1d"]
            run([source / "configure", *options], cwd=build, env=env)
            config = "\n".join((build / file).read_text(encoding="utf-8")
                               for file in ("config.h", "config_components.h"))
            for capability in ("CONFIG_LIBSRT", "CONFIG_LIBDAV1D", "CONFIG_GNUTLS", "CONFIG_RTSP_DEMUXER",
                               "CONFIG_RTMP_PROTOCOL", "CONFIG_LIBSRT_PROTOCOL", "CONFIG_VIDEOTOOLBOX",
                               "CONFIG_ZLIB", "CONFIG_BZLIB", "CONFIG_ICONV"):
                if f"#define {capability} 1" not in config:
                    raise RuntimeError(f"FFmpeg 未启用必需能力: {capability}")
            run(["make", f"-j{jobs}"], cwd=build, env=env)
            run(["make", "install"], cwd=build, env=env)
        else:
            options = [f"--prefix={prefix}", f"--host={host}", "--enable-static", "--disable-shared"]
            options += {
                "gmp": ["--disable-assembly", "--with-pic", "--disable-maintainer-mode"],
                "nettle": ["--disable-assembler", "--disable-openssl", "--disable-documentation", "--enable-pic"],
                "gnutls": ["--with-included-libtasn1", "--with-included-unistring", "--without-idn", "--without-p11-kit",
                           "--without-brotli", "--without-zstd", "--without-zlib", "--disable-hardware-acceleration",
                           "--disable-tools", "--disable-doc", "--disable-tests", "--disable-nls", "--disable-cxx",
                           "--disable-maintainer-mode", "--with-pic"],
                "libass": ["--disable-fontconfig", "--disable-require-system-font-provider", "--disable-test",
                           "--disable-profile", "--with-pic"],
            }[name]
            run([source / "configure", *options], cwd=build, env=env)
            run(["make", f"-j{jobs}"], cwd=build, env=env)
            run(["make", "install"], cwd=build, env=env)
    return prefix


def framework(module, library, header_dir, sdk, arches):
    destination = WORK / "frameworks" / sdk / (module + ".framework")
    if destination.exists():
        shutil.rmtree(destination)
    headers = destination / "Headers"
    headers.mkdir(parents=True)
    prefix = WORK / "slices" / f"{sdk}-{arches[0]}" / "prefix"
    if header_dir:
        shutil.copytree(prefix / "include" / header_dir, headers, dirs_exist_ok=True)
    else:
        shutil.copyfile(prefix / "include/gmp.h", headers / "gmp.h")
    run(["lipo", "-create", *[WORK / "slices" / f"{sdk}-{arch}" / "prefix/lib" / f"lib{library}.a" for arch in arches],
         "-output", destination / module])
    excluded = {
        "Libavcodec": ("amfdec", "amfenc", "qsv", "vdpau", "dxva2", "d3d11va", "d3d12va", "vulkan_video", "videotoolbox", "mediacodec"),
        "Libavutil": ("hwcontext_amf", "hwcontext_amf_internal", "hwcontext_vulkan", "hwcontext_vdpau", "hwcontext_vaapi", "hwcontext_qsv", "hwcontext_opencl",
                      "hwcontext_dxva2", "hwcontext_d3d11va", "hwcontext_d3d12va", "hwcontext_cuda", "hwcontext_videotoolbox",
                      "hwcontext_drm", "hwcontext_mediacodec", "hwcontext_ohcodec"),
        "Libavfilter": ("vf_scale_cuda",),
    }.get(module, ())
    modules = destination / "Modules"
    modules.mkdir()
    modulemap = f'framework module {module} [system] {{\n  umbrella "."\n'
    modulemap += "".join(f'  exclude header "{name}.h"\n' for name in excluded if (headers / f"{name}.h").exists())
    (modules / "module.modulemap").write_text(modulemap + "  export *\n}\n", encoding="utf-8")
    info = {"CFBundleExecutable": module, "CFBundleIdentifier": "org.ohmymedia.native." + module,
            "CFBundleName": module, "CFBundlePackageType": "FMWK", "CFBundleVersion": "8.1.0",
            "CFBundleShortVersionString": "8.1.0", "MinimumOSVersion": LOCK["ios_minimum"],
            "CFBundleSupportedPlatforms": ["iPhoneOS" if sdk == "iphoneos" else "iPhoneSimulator"]}
    with (destination / "Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    return destination


def build(jobs):
    if platform.system() != "Darwin":
        raise RuntimeError("iOS 原生编译需要 macOS 和 Xcode；可在其它系统运行 prepare、plan 和测试")
    for tool in ("xcodebuild", "xcrun", "make", "meson", "ninja", "cmake", "nasm", "pkg-config"):
        if not shutil.which(tool):
            raise RuntimeError(f"缺少构建工具: {tool}，请按 README 安装工具链")
    key = cache_key()
    prepare()
    stamp = WORK / "build-complete.json"
    if stamp.exists() and json.loads(stamp.read_text())["cache_key"] == key:
        expected = WORK / "pods/FFmpegKit/Frameworks"
        pod_files = ("FFmpegKit/FFmpegKit.podspec",
                     "FFmpegKit/Libass.podspec", "FFmpegKit/Sources/FFmpegKit/include/avformat_shim.h",
                     "FFmpegKit/native-versions.json")
        if (all((expected / (module + ".xcframework/Info.plist")).is_file() for module in LIBRARIES)
                and all((WORK / "pods" / file).is_file() for file in pod_files)):
            print("本地依赖已匹配组件源码与工具链缓存。")
            return
    for sdk, arch in SLICES:
        build_slice(sdk, arch, jobs)
    output = WORK / "pods/FFmpegKit/Frameworks"
    output.mkdir(parents=True, exist_ok=True)
    for module, (library, header_dir) in LIBRARIES.items():
        device = framework(module, library, header_dir, "iphoneos", ["arm64"])
        simulator = framework(module, library, header_dir, "iphonesimulator", ["arm64", "x86_64"])
        target = output / (module + ".xcframework")
        if target.exists():
            shutil.rmtree(target)
        run(["xcodebuild", "-create-xcframework", "-framework", device, "-framework", simulator, "-output", target])
    stamp.write_text(json.dumps({"cache_key": key, "ffmpeg": LOCK["ffmpeg_version"]}) + "\n", encoding="utf-8")


def main():
    global WORK, COMPONENTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "build", "cache-key", "release-key", "plan"))
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--components-dir", type=Path, default=COMPONENTS,
                        help="独立 FFmpeg 源码仓库的父目录")
    parser.add_argument("--output", type=Path, default=WORK, help="构建输出目录")
    args = parser.parse_args()
    WORK = args.output.resolve()
    COMPONENTS = args.components_dir.resolve()
    if args.command == "prepare":
        prepare()
    elif args.command == "build":
        build(args.jobs)
    elif args.command == "cache-key":
        print(cache_key())
    elif args.command == "release-key":
        print(json.dumps(release_key(), sort_keys=True))
    else:
        print(json.dumps({"sources": LOCK, "slices": SLICES, "build_order": ORDER,
                          "modules": list(LIBRARIES), "output": str(WORK / "pods")}, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
