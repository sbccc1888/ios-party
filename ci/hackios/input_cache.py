#!/usr/bin/env python3
"""Cache immutable downloads; never cache a kernel or a runtime qualification."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


APPLE_FILES = {
    "apple_rootfs": "096-04088-077.dmg",
    "apple_cryptex": "096-04666-078.dmg",
    "apple_app": "096-03536-083.dmg",
}
TOOLCHAIN_FILE = "iOSToolchain-x86_64.tar.xz"


def file_digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(4 << 20):
            sha.update(data)
    return {"bytes": path.stat().st_size, "sha256": sha.hexdigest()}


def checked_record(record):
    size, sha = record.get("bytes"), record.get("sha256")
    if type(size) is not int or size < 0 or not isinstance(sha, str):
        raise ValueError("invalid pinned download record")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("invalid pinned download SHA-256")
    return {"bytes": size, "sha256": sha}


def matches(path, expected):
    expected = checked_record(expected)
    if path.is_symlink() or not path.is_file():
        return False
    return path.stat().st_size == expected["bytes"] and file_digest(path) == expected


class InputCache:
    def __init__(self, directory):
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("cache directory must not be a symlink")
        self.directory = directory.parent.resolve() / directory.name
        self.events = []

    def path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_,.-]+", name):
            raise ValueError("cache names must be single safe filenames")
        if name in (".", ".."):
            raise ValueError("invalid cache filename")
        # Cache directories are runner-owned, never source-controlled symlinks.
        if self.directory.is_symlink():
            raise ValueError("cache directory must not contain symlinks")
        return self.directory / name

    @staticmethod
    def _materialize(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)

    def restore(self, name, destination, expected):
        source, destination = self.path(name), Path(destination)
        checked_record(expected)
        if destination.exists() or destination.is_symlink():
            if not matches(destination, expected):
                raise ValueError(f"existing download has the wrong identity: {destination}")
            self.events.append({"file": name, "status": "workspace-verified"})
            return True
        if not matches(source, expected):
            status = "rejected" if source.exists() or source.is_symlink() else "miss"
            self.events.append({"file": name, "status": status})
            return False
        self._materialize(source, destination)
        self.events.append({"file": name, "status": "hit"})
        return True

    def publish(self, name, source, expected):
        destination, source = self.path(name), Path(source)
        if not matches(source, expected):
            self.events.append({"file": name, "status": "not-verified"})
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        if not matches(destination, expected):
            with tempfile.TemporaryDirectory(prefix=".publish-", dir=self.directory) as temporary:
                staged = Path(temporary) / name
                self._materialize(source, staged)
                staged.replace(destination)
        self.events.append({"file": name, "status": "published"})
        return True


def records(dependencies, apple_lock):
    toolchain = dependencies["toolchain"]
    toolchain = checked_record({
        "bytes": toolchain["asset_bytes"], "sha256": toolchain["sha256"],
    })
    if apple_lock["product_version"] != "16.5" or apple_lock["build_version"] != "20F66":
        raise ValueError("the input cache only accepts the pinned 20F66 recipe")
    members = {name: checked_record(record) for name, record in apple_lock["members"].items()}
    for name in (*APPLE_FILES.values(), "BuildManifest.plist"):
        if name not in members:
            raise ValueError(f"missing pinned Apple input: {name}")
    return toolchain, members


def cache_keys(dependencies, apple_lock, platform):
    toolchain, members = records(dependencies, apple_lock)
    result = {"toolchain_key": f"{platform}-hackios-toolchain-v1-{toolchain['sha256']}"}
    for label, name in APPLE_FILES.items():
        result[label + "_key"] = f"{platform}-hackios-20F66-v1-{members[name]['sha256']}"
    return result


def cache_status(root, dependencies, apple_lock):
    toolchain, members = records(dependencies, apple_lock)
    dependency_cache = InputCache(Path(root) / "toolchain")
    apple_cache = InputCache(Path(root) / "apple" / "20F66")
    result = {"toolchain_ready": matches(dependency_cache.path(TOOLCHAIN_FILE), toolchain)}
    for label, name in APPLE_FILES.items():
        result[label + "_ready"] = matches(apple_cache.path(name), members[name])
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("keys", "status"))
    parser.add_argument("--dependencies", type=Path, default=root / "ci/hackios/dependencies.lock.json")
    parser.add_argument("--apple-lock", type=Path,
                        default=root / "research/hackios-reuse/cloud-inputs-20F66.json")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--platform", default=f"{os.environ.get('RUNNER_OS', 'Linux')}-{os.environ.get('RUNNER_ARCH', 'X64')}")
    parser.add_argument("--output", type=Path, help="Append GitHub Actions step outputs")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    dependencies = json.loads(args.dependencies.read_text())
    apple_lock = json.loads(args.apple_lock.read_text())
    if args.action == "status":
        if args.root is None:
            parser.error("status requires --root")
        result = cache_status(args.root, dependencies, apple_lock)
    else:
        result = cache_keys(dependencies, apple_lock, args.platform)
    if args.output:
        with args.output.open("a") as stream:
            for name, value in result.items():
                stream.write(f"{name}={str(value).lower() if isinstance(value, bool) else value}\n")
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
