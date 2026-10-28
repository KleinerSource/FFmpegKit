#!/usr/bin/env python3
"""更新唯一的 iOS 组件 Release，上传成功后清理旧 Release，保留 Git 标签。"""

import hashlib
import json
import re
import subprocess

import build as native


REPOSITORY = "KleinerSource/FFmpegKit"


def gh(*args):
    return subprocess.run(["gh", *map(str, args)], check=True, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8").stdout


def publish(directory):
    assets = [directory / "ios-native-pods.zip", directory / "ios-native-pods.zip.sha256",
              directory / "distribution/release-manifest.json"]
    for asset in assets:
        if not asset.is_file():
            raise ValueError(f"Release 制品缺失: {asset}")
    manifest = json.loads(assets[2].read_text(encoding="utf-8"))
    tag = native.RELEASE_TAG
    if manifest.get("tag") != tag:
        raise ValueError("Release 清单必须使用固定标签 ios-native-latest")
    target = manifest["sources"]["FFmpegKit"]
    if not re.fullmatch(r"[0-9a-f]{40}", target):
        raise ValueError("Release 的 FFmpegKit 提交无效")
    with assets[0].open("rb") as archive:
        digest = hashlib.file_digest(archive, "sha256").hexdigest()
    if assets[1].read_text(encoding="ascii").strip() != f"{digest}  ios-native-pods.zip":
        raise ValueError("Release 归档 SHA-256 校验失败")

    tags = gh("api", f"repos/{REPOSITORY}/releases", "--paginate", "--jq", ".[].tag_name").splitlines()
    metadata = ("--repo", REPOSITORY, "--title", "iOS native components (latest)",
                "--notes", "Latest XCFrameworks. Exact sources and ABI versions are in release-manifest.json.",
                "--latest")
    if tag in tags:
        gh("release", "upload", tag, *assets, "--repo", REPOSITORY, "--clobber")
        gh("api", "--method", "PATCH", f"repos/{REPOSITORY}/git/refs/tags/{tag}",
           "-f", f"sha={target}", "-F", "force=true")
        gh("release", "edit", tag, *metadata, "--draft=false", "--prerelease=false")
    else:
        gh("release", "create", tag, *assets, "--target", target, *metadata)
    for old_tag in tags:
        if old_tag != tag and not old_tag.startswith("libmpv-"):
            gh("release", "delete", old_tag, "--repo", REPOSITORY, "--yes")


if __name__ == "__main__":
    publish(native.WORK)
