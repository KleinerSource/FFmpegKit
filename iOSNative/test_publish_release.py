import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_release as publisher


class RollingReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ios-native-publish-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tag = "ios-native-latest"
        self.target = "f" * 40
        archive = b"built and validated native components"
        (self.root / "ios-native-pods.zip").write_bytes(archive)
        (self.root / "ios-native-pods.zip.sha256").write_text(
            f"{hashlib.sha256(archive).hexdigest()}  ios-native-pods.zip\n", encoding="ascii")
        (self.root / "distribution").mkdir()
        (self.root / "distribution/release-manifest.json").write_text(json.dumps({
            "tag": self.tag, "sources": {"FFmpegKit": self.target},
        }), encoding="utf-8")

    def test_first_rolling_release_is_created_before_removing_old_releases(self):
        with patch.object(publisher, "gh", side_effect=["ios-native-old\nn8.1\n", "", "", ""]) as gh:
            publisher.publish(self.root)
        calls = [call.args for call in gh.call_args_list]
        self.assertEqual(calls[1][:3], ("release", "create", self.tag))
        self.assertIn(self.target, calls[1])
        self.assertEqual([call[2] for call in calls[2:]], ["ios-native-old", "n8.1"])
        self.assertTrue(all("--cleanup-tag" not in call for call in calls))

    def test_existing_release_is_overwritten_without_deleting_the_latest(self):
        with patch.object(publisher, "gh", side_effect=[f"{self.tag}\nios-native-old\n", "", "", "", ""]) as gh:
            publisher.publish(self.root)
        calls = [call.args for call in gh.call_args_list]
        self.assertEqual(calls[1][:3], ("release", "upload", self.tag))
        self.assertIn("--clobber", calls[1])
        self.assertIn(f"sha={self.target}", calls[2])
        self.assertEqual(calls[3][:3], ("release", "edit", self.tag))
        self.assertEqual(calls[4][:3], ("release", "delete", "ios-native-old"))
        self.assertFalse(any(call[:3] == ("release", "delete", self.tag) for call in calls))

    def test_failed_upload_does_not_remove_any_release(self):
        error = subprocess.CalledProcessError(1, ["gh", "release", "upload"])
        with patch.object(publisher, "gh", side_effect=[f"{self.tag}\nios-native-old\n", error]) as gh:
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.publish(self.root)
        self.assertEqual(gh.call_count, 2)

    def test_failed_creation_does_not_remove_any_release(self):
        error = subprocess.CalledProcessError(1, ["gh", "release", "create"])
        with patch.object(publisher, "gh", side_effect=["ios-native-old\n", error]) as gh:
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.publish(self.root)
        self.assertEqual(gh.call_count, 2)

    def test_bad_checksum_stops_before_remote_mutations(self):
        (self.root / "ios-native-pods.zip").write_bytes(b"truncated")
        with patch.object(publisher, "gh") as gh:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                publisher.publish(self.root)
        gh.assert_not_called()

    def test_missing_asset_stops_before_remote_mutations(self):
        (self.root / "distribution/release-manifest.json").unlink()
        with patch.object(publisher, "gh") as gh:
            with self.assertRaisesRegex(ValueError, "制品缺失"):
                publisher.publish(self.root)
        gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
