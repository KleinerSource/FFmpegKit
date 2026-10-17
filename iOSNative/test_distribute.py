import importlib.util
from pathlib import Path
import plistlib
import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("ios_native_distribute", Path(__file__).with_name("distribute.py"))
distribute = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribute)


class StaticFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ios-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


    def test_source_and_prebuilt_pods_force_load_ffmpeg_with_media_kit(self):
        for name in ("FFmpegKit",):
            (self.root / name).mkdir()
        distribute.podspecs(self.root)
        specs = [distribute.native.ROOT / "FFmpegKit.podspec",
                 self.root / "FFmpegKit/FFmpegKit.podspec"]
        for spec in specs:
            text = spec.read_text(encoding="utf-8")
            with self.subTest(spec=spec):
                self.assertIn("s.user_target_xcconfig", text)
                self.assertIn("'OTHER_LDFLAGS' => '$(inherited) $(FFMPEGKIT_FORCE_LOAD_FLAGS)'", text)
                self.assertIn("-Wl,-force_load,", text)
                self.assertNotIn('-force_load \\"', text)
                self.assertNotIn("'-force_load ", text)
                self.assertNotIn("PODS_XCFRAMEWORKS_BUILD_DIR", text)
                self.assertIn("force_load.call('ios-arm64')", text)
                self.assertIn("force_load.call('ios-arm64_x86_64-simulator')", text)
                self.assertIn("%w[Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale]", text)

    @unittest.skipUnless(shutil.which("ruby"), "Ruby is required to evaluate CocoaPods settings")
    def test_force_load_flags_resolve_to_existing_device_and_simulator_archives(self):
        ruby = '''require 'json'
module Pod
  class Spec
    def self.new
      spec = allocate
      spec.instance_variable_set(:@settings, {})
      yield spec
      puts JSON.generate(spec.instance_variable_get(:@settings)['user_target_xcconfig'])
      spec
    end
    def method_missing(name, *args)
      key = name.to_s
      if key.end_with?('=')
        @settings[key.chop] = args.first
      else
        @settings.fetch(key, self)
      end
    end
  end
end
load ARGV.first
'''
        # Spaces and UTF-8 must survive both Ruby interpolation and linker quoting.
        pods = self.root / "native pods 中文"
        (pods / "KSPlayer").mkdir(parents=True)
        (pods / "FFmpegKit").mkdir()
        distribute.podspecs(pods)
        source = pods / "source"
        source.mkdir()
        shutil.copyfile(distribute.native.ROOT / "FFmpegKit.podspec", source / "FFmpegKit.podspec")
        names = ("Libavcodec", "Libavdevice", "Libavfilter", "Libavformat",
                 "Libavutil", "Libswresample", "Libswscale")
        slices = {"iphoneos": "ios-arm64", "iphonesimulator": "ios-arm64_x86_64-simulator"}
        for root in (source, pods / "FFmpegKit"):
            for name in names:
                for slice_name in slices.values():
                    binary = root / "Frameworks" / f"{name}.xcframework" / slice_name / f"{name}.framework" / name
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(b"static archive")
            settings = json.loads(subprocess.check_output(
                ["ruby", "-EUTF-8", "-e", ruby, str(root / "FFmpegKit.podspec")], encoding="utf-8"))
            for sdk, slice_name in slices.items():
                flags = shlex.split(settings[f"FFMPEGKIT_FORCE_LOAD_FLAGS[sdk={sdk}*]"])
                self.assertEqual(len(flags), len(names))
                for flag in flags:
                    self.assertTrue(flag.startswith("-Wl,-force_load,"))
                    archive = Path(flag.split(",", 2)[2])
                    self.assertTrue(archive.is_file(), archive)
                    self.assertIn(slice_name, archive.parts)

    def test_wraps_clang_static_library_with_public_module_map(self):
        library = self.root / "libDisplayCriteria.a"
        library.write_bytes(b"archive")
        header = self.root / "AVDisplayCriteriaKS.h"
        header.write_text("@interface AVDisplayCriteria (KSPlayer)\n@end\n", encoding="utf-8")
        with patch.object(distribute.native, "WORK", self.root):
            framework = distribute.static_framework("DisplayCriteria", library,
                                                    "iphonesimulator", [header])
        modulemap = (framework / "Modules/module.modulemap").read_text(encoding="utf-8")
        self.assertIn('framework module DisplayCriteria', modulemap)
        self.assertIn('umbrella header "DisplayCriteria.h"', modulemap)
        self.assertTrue((framework / "Headers/AVDisplayCriteriaKS.h").is_file())
        self.assertTrue((framework / "Headers/DisplayCriteria.h").is_file())

    def test_ffmpegkit_umbrella_header_is_in_framework_headers(self):
        library = self.root / "libFFmpegKit.a"
        library.write_bytes(b"archive")
        headers = []
        for name in ("avcodec_shim.h", "avformat_shim.h"):
            header = self.root / name
            header.write_text("", encoding="utf-8")
            headers.append(header)
        with patch.object(distribute.native, "WORK", self.root):
            framework = distribute.static_framework("FFmpegKit", library,
                                                    "iphoneos", headers)
        modulemap = (framework / "Modules/module.modulemap").read_text(encoding="utf-8")
        self.assertIn('umbrella header "FFmpegKit.h"', modulemap)
        self.assertTrue((framework / "Headers/FFmpegKit.h").is_file())
        self.assertIn('#import "avcodec_shim.h"',
                      (framework / "Headers/FFmpegKit.h").read_text(encoding="utf-8"))

    def test_archives_xcode_target_object_when_library_is_not_emitted(self):
        products = self.root / "Products"
        products.mkdir()
        object_file = products / "FFmpegKit.o"
        object_file.write_bytes(b"object")

        def create_archive(args, cwd):
            Path(args[3]).write_bytes(b"archive")

        with patch.object(distribute, "run", side_effect=create_archive) as run:
            library = distribute.static_library("FFmpegKit", products, self.root)

        self.assertEqual(library.read_bytes(), b"archive")
        run.assert_called_once_with(("libtool", "-static", "-o", library, object_file), self.root)

    def test_generates_native_package_without_player_sources(self):
        ffmpegkit = self.root / "FFmpegKit"
        (ffmpegkit / "Sources/FFmpegKit/include").mkdir(parents=True)
        (ffmpegkit / "Sources/FFmpegKit/include/avcodec_shim.h").write_text("", encoding="utf-8")
        native_work = self.root / "build/ios-native"
        framework_root = native_work / "pods/FFmpegKit/Frameworks"
        for name in distribute.CLANG_MODULES:
            path = framework_root / f"{name}.xcframework"
            path.mkdir(parents=True)
            (path / "Info.plist").write_text("fixture", encoding="utf-8")

        with (patch.object(distribute.native, "ROOT", ffmpegkit),
              patch.object(distribute.native, "WORK", native_work)):
            package_root = distribute.package_sources()

        native_manifest = (package_root / "packages/Native/Package.swift").read_text(encoding="utf-8")
        self.assertIn('name: "FFmpegKit"', native_manifest)
        self.assertIn('.library(name: "FFmpegKit", type: .static', native_manifest)
        self.assertEqual([path.name for path in (package_root / "packages").iterdir()], ["Native"])
        for name in distribute.CLANG_MODULES:
            self.assertIn(f'Frameworks/{name}.xcframework', native_manifest)

    def test_distribution_contains_only_native_pods_and_matching_metadata(self):
        kit = self.root / "pods/FFmpegKit"
        (kit / "Licenses/libass").mkdir(parents=True)
        (kit / "Licenses/libass/COPYING").write_text("license", encoding="utf-8")
        (kit / "LICENSE").write_text("license", encoding="utf-8")
        for name in distribute.native.LIBRARIES:
            framework = kit / "Frameworks" / f"{name}.xcframework"
            framework.mkdir(parents=True)
            (framework / "Info.plist").write_bytes(plistlib.dumps({"fixture": name}))
        wrapper = self.root / "FFmpegKit.xcframework"
        wrapper.mkdir()
        (wrapper / "Info.plist").write_bytes(plistlib.dumps({"fixture": "FFmpegKit"}))
        sources = {"FFmpegKit": "f" * 40, "Libass": "f" * 40}
        (kit / "native-versions.json").write_text(json.dumps({
            "components": sources, "ffmpeg_version": "n8.1", "libav_versions": {"libavcodec": "62.28.100"},
        }), encoding="utf-8")
        identity = {"tag": "ios-native-latest", "ffmpegkit_revision": "f" * 40, "native_cache_key": "b" * 64}
        with (patch.object(distribute.native, "WORK", self.root),
              patch.object(distribute.native, "release_key", return_value=identity)):
            distribute.assemble({"FFmpegKit": wrapper})
        with zipfile.ZipFile(self.root / "ios-native-pods.zip") as archive:
            self.assertTrue(any(name.startswith("pods/FFmpegKit/") for name in archive.namelist()))
            self.assertFalse(any(name.startswith("pods/KSPlayer/") for name in archive.namelist()))
            manifest = json.loads(archive.read("release-manifest.json"))
            self.assertEqual(manifest["frameworks"], ["FFmpegKit", "Libass"])
            self.assertEqual(manifest["sources"], sources)
            self.assertEqual(manifest["schema"], 2)


if __name__ == "__main__":
    unittest.main()
