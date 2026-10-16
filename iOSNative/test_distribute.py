import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("ios_native_distribute", Path(__file__).with_name("distribute.py"))
distribute = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribute)


class StaticFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ios-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_wraps_swift_static_library_with_module_interface(self):
        library = self.root / "libKSPlayer.a"
        library.write_bytes(b"archive")
        module = self.root / "KSPlayer.swiftmodule"
        module.mkdir()
        (module / "arm64-apple-ios.swiftinterface").write_text("public struct Player {}\n", encoding="utf-8")
        with patch.object(distribute.native, "WORK", self.root):
            framework = distribute.static_framework("KSPlayer", library, module, "iphoneos")
        self.assertEqual((framework / "KSPlayer").read_bytes(), b"archive")
        self.assertTrue((framework / "Modules/KSPlayer.swiftmodule/arm64-apple-ios.swiftinterface").is_file())
        with (framework / "Info.plist").open("rb") as source:
            self.assertEqual(plistlib.load(source)["MachOType"], "staticlib")

    def test_source_and_prebuilt_pods_force_load_ffmpeg_with_media_kit(self):
        for name in ("KSPlayer", "FFmpegKit"):
            (self.root / name).mkdir()
        distribute.podspecs(self.root)
        specs = [distribute.native.ROOT / "FFmpegKit.podspec",
                 self.root / "FFmpegKit/FFmpegKit.podspec"]
        for spec in specs:
            text = spec.read_text(encoding="utf-8")
            with self.subTest(spec=spec):
                self.assertIn("s.user_target_xcconfig", text)
                self.assertIn("'OTHER_LDFLAGS' => '$(inherited) '", text)
                self.assertIn("-force_load", text)
                self.assertIn("$(PODS_XCFRAMEWORKS_BUILD_DIR)/FFmpegKit/", text)
                self.assertIn("%w[Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale]", text)

    def test_wraps_clang_static_library_with_public_module_map(self):
        library = self.root / "libDisplayCriteria.a"
        library.write_bytes(b"archive")
        header = self.root / "AVDisplayCriteriaKS.h"
        header.write_text("@interface AVDisplayCriteria (KSPlayer)\n@end\n", encoding="utf-8")
        with patch.object(distribute.native, "WORK", self.root):
            framework = distribute.static_framework("DisplayCriteria", library, None,
                                                    "iphonesimulator", [header])
        modulemap = (framework / "Modules/module.modulemap").read_text(encoding="utf-8")
        self.assertIn('framework module DisplayCriteria', modulemap)
        self.assertIn('umbrella header "DisplayCriteria.h"', modulemap)
        self.assertTrue((framework / "Headers/AVDisplayCriteriaKS.h").is_file())

    def test_generates_static_packages_from_current_component_sources(self):
        ffmpegkit = self.root / "FFmpegKit"
        (ffmpegkit / "Sources/FFmpegKit/include").mkdir(parents=True)
        (ffmpegkit / "Sources/FFmpegKit/include/avcodec_shim.h").write_text("", encoding="utf-8")
        components = self.root / "components"
        player_sources = components / "KSPlayer/Sources"
        (player_sources / "KSPlayer/Metal").mkdir(parents=True)
        (player_sources / "KSPlayer/Metal/Shaders.metal").write_text("", encoding="utf-8")
        (player_sources / "DisplayCriteria/include").mkdir(parents=True)
        (player_sources / "DisplayCriteria/include/AVDisplayCriteriaKS.h").write_text("", encoding="utf-8")
        (player_sources / "DisplayCriteria/DisplayCriteria.m").write_text("", encoding="utf-8")
        native_work = self.root / "build/ios-native"
        framework_root = native_work / "pods/FFmpegKit/Frameworks"
        for name in distribute.CLANG_MODULES:
            path = framework_root / f"{name}.xcframework"
            path.mkdir(parents=True)
            (path / "Info.plist").write_text("fixture", encoding="utf-8")

        with (patch.object(distribute.native, "ROOT", ffmpegkit),
              patch.object(distribute.native, "COMPONENTS", components),
              patch.object(distribute.native, "WORK", native_work)):
            package_root = distribute.package_sources()

        native_manifest = (package_root / "packages/Native/Package.swift").read_text(encoding="utf-8")
        display_manifest = (package_root / "packages/DisplayCriteria/Package.swift").read_text(encoding="utf-8")
        player_manifest = (package_root / "packages/KSPlayer/Package.swift").read_text(encoding="utf-8")
        self.assertIn('.library(name: "FFmpegKit", type: .static', native_manifest)
        self.assertIn('.library(name: "DisplayCriteria", type: .static', display_manifest)
        self.assertIn('.library(name: "KSPlayer", type: .static', player_manifest)
        self.assertIn('.package(name: "FFmpegKit", path: "../Native")', player_manifest)
        self.assertIn('.process("Metal/Shaders.metal")', player_manifest)
        for name in distribute.CLANG_MODULES:
            self.assertIn(f'Frameworks/{name}.xcframework', native_manifest)


if __name__ == "__main__":
    unittest.main()
