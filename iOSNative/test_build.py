import importlib.util
import io
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("ios_native_build", Path(__file__).with_name("build.py"))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class SourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ios-native-中文-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, name, content=b"source", link=None):
        archive = self.root / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            member = tarfile.TarInfo(name)
            if link is not None:
                member.type = tarfile.SYMTYPE
                member.linkname = link
                output.addfile(member)
            else:
                member.size = len(content)
                output.addfile(member, io.BytesIO(content))
        return archive

    def test_extracts_valid_source_and_rejects_escape(self):
        destination = self.root / "sources"
        destination.mkdir()
        native.extract(self.archive("lib/中文.c"), destination)
        self.assertEqual((destination / "lib/中文.c").read_bytes(), b"source")
        with self.assertRaises(RuntimeError):
            native.extract(self.archive("../outside"), destination)
        self.assertFalse((self.root / "outside").exists())
        with self.assertRaises(RuntimeError):
            native.extract(self.archive("link", link="../../outside"), destination)

    def test_bad_download_hash_stops_before_extracting(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        archive = self.archive("source/file.c")
        (downloads / archive.name).write_bytes(archive.read_bytes())
        lock = {"sources": {"library": {"url": "https://invalid.test/source.tar.gz", "sha256": "0" * 64}}}
        with patch.object(native, "WORK", self.root), patch.object(native, "LOCK", lock):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                native.fetch("library")
        self.assertFalse((self.root / "library").exists())

    def test_exports_pinned_commit_and_ignores_dirty_worktree(self):
        source = self.root / "components/library"
        source.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
        (source / "value.c").write_text("committed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "value.c"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        (source / "value.c").write_text("dirty\n", encoding="utf-8")
        lock = {"sources": {"library": {"git": "https://invalid.test/library", "commit": commit}}}
        output = self.root / "export"
        with (patch.object(native, "COMPONENTS", self.root / "components"),
              patch.object(native, "WORK", self.root), patch.object(native, "LOCK", lock)):
            native.export_git("library", output)
            self.assertEqual((output / "value.c").read_text(encoding="utf-8"), "committed\n")
            lock["sources"]["library"]["commit"] = "0" * 40
            original_run = native.run
            def fail_fetch(args, **kwargs):
                if "fetch" in args:
                    raise subprocess.CalledProcessError(128, args)
                return original_run(args, **kwargs)
            with patch.object(native, "run", side_effect=fail_fetch):
                with self.assertRaises(subprocess.CalledProcessError):
                    native.export_git("library", output)
        self.assertEqual((source / "value.c").read_text(encoding="utf-8"), "dirty\n")

    def test_slice_environment_separates_host_and_ios_compilers(self):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout

        def fake_run(args, **kwargs):
            if "--show-sdk-path" in args:
                if "macosx" in args:
                    return Result("/path/to/MacOSX.sdk\n")
                return Result("/path/to/iPhoneOS.sdk\n")
            if "--find" in args and "clang" in args:
                return Result("/usr/bin/clang\n")
            raise AssertionError(args)

        with (patch.object(native, "run", side_effect=fake_run),
              patch.object(native.shutil, "which", return_value="/usr/bin/clang")):
            env, _, sdkroot = native.slice_environment(
                "iphoneos", "arm64", self.root / "prefix"
            )
        self.assertEqual(sdkroot, "/path/to/iPhoneOS.sdk")
        self.assertEqual(env["CC"], "/usr/bin/clang")
        self.assertEqual(
            env["CC_FOR_BUILD"], "/usr/bin/clang -isysroot /path/to/MacOSX.sdk"
        )
        self.assertEqual(env["HOST_CC"], env["CC_FOR_BUILD"])
        self.assertEqual(env["CPP_FOR_BUILD"], "")
        self.assertEqual(env["CFLAGS_FOR_BUILD"], "")
        self.assertEqual(env["SDKROOT"], "")
        self.assertEqual(env["IPHONEOS_DEPLOYMENT_TARGET"], "")
        self.assertIn("-target arm64-apple-ios", env["CFLAGS"])
        self.assertIn("-stdlib=libc++", env["CXXFLAGS"])

    def test_harfbuzz_setup_preserves_target_flags_and_disables_pragma_errors(self):
        for sdk, arch in native.SLICES:
            with self.subTest(sdk=sdk, arch=arch):
                sdkroot = "/SDK with spaces/iOS.sdk"
                target = f"{arch}-apple-ios16.0" + ("-simulator" if sdk == "iphonesimulator" else "")
                flags = ["-target", target, "-isysroot", sdkroot, "-fPIC"]
                env = {"CC": "clang", "CXX": "clang++",
                       "CFLAGS": shlex.join(flags),
                       "CXXFLAGS": shlex.join(flags + ["-stdlib=libc++"])}
                with (patch.object(native, "WORK", self.root),
                      patch.object(native, "ORDER", ("harfbuzz",)),
                      patch.object(native, "slice_environment", return_value=(env, flags, sdkroot)),
                      patch.object(native, "run") as run):
                    native.build_slice(sdk, arch, 3)
                setup = run.call_args_list[0].args[0]
                for language in ("c", "cpp"):
                    option = next(arg for arg in setup if str(arg).startswith(f"-D{language}_args="))
                    effective_flags = shlex.split(option.split("=", 1)[1])
                    self.assertEqual(effective_flags[:len(flags)], flags)
                    self.assertIn("-DHB_NO_PRAGMA_GCC_DIAGNOSTIC_ERROR", effective_flags)
                    if language == "cpp":
                        self.assertIn("-stdlib=libc++", effective_flags)

    def test_native_cache_reuses_libraries_for_player_only_changes(self):
        lock = deepcopy(native.LOCK)
        with (patch.object(native, "LOCK", lock),
              patch.object(native.platform, "system", return_value="Windows")):
            original = native.cache_key()
            lock["sources"]["KSPlayer"]["commit"] = "0" * 40
            self.assertEqual(native.cache_key(), original)
            lock["sources"]["FFmpeg"]["commit"] = "0" * 40
            self.assertNotEqual(native.cache_key(), original)

    def test_cached_build_refreshes_player_sources_without_compiling(self):
        (self.root / "build-complete.json").write_text(
            json.dumps({"cache_key": "fixture"}), encoding="utf-8"
        )
        for module in native.LIBRARIES:
            info = self.root / "pods/FFmpegKit/Frameworks" / f"{module}.xcframework/Info.plist"
            info.parent.mkdir(parents=True)
            info.touch()

        def prepare():
            for file in ("KSPlayer/KSPlayer.podspec", "KSPlayer/DisplayCriteria.podspec",
                         "FFmpegKit/FFmpegKit.podspec", "FFmpegKit/Libass.podspec",
                         "FFmpegKit/Sources/FFmpegKit/include/avformat_shim.h",
                         "FFmpegKit/native-versions.json"):
                path = self.root / "pods" / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("current source", encoding="utf-8")

        with (patch.object(native, "WORK", self.root),
              patch.object(native.platform, "system", return_value="Darwin"),
              patch.object(native.shutil, "which", return_value="fixture"),
              patch.object(native, "cache_key", return_value="fixture"),
              patch.object(native, "prepare", side_effect=prepare),
              patch.object(native, "build_slice") as compile_slice):
            native.build(3)
        compile_slice.assert_not_called()
        self.assertEqual((self.root / "pods/KSPlayer/KSPlayer.podspec").read_text(encoding="utf-8"),
                         "current source")


if __name__ == "__main__":
    unittest.main()
