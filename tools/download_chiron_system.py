#!/usr/bin/env python3
"""Resume the generated private system-volume artifact with bounded HTTP ranges."""

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", default="12484179406")
    ap.add_argument("--output", type=Path, default=Path("dist/mix2-hackios-system"))
    ap.add_argument("--cache", type=Path, default=Path(".cache/chiron-system-download"))
    ap.add_argument("--partial", type=Path)
    ap.add_argument("--adopt-gh-pid", type=int)
    ap.add_argument("--jobs", type=int, default=16, choices=range(1, 33))
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    archive = args.cache / "artifact.zip"
    journal = args.cache / "ranges.json"
    prefix = 0
    if args.partial and not archive.exists():
        with args.partial.open("rb") as source:
            if source.read(2) != b"PK":
                raise RuntimeError("partial file is not the existing artifact ZIP")
        os.link(args.partial, archive)
        if args.adopt_gh_pid:
            cmd = subprocess.check_output(["ps", "-p", str(args.adopt_gh_pid), "-o", "args="], text=True)
            if "gh run download 39915264473" not in cmd:
                raise RuntimeError("refusing to stop a process other than this task's old download")
            os.kill(args.adopt_gh_pid, signal.SIGTERM)
            time.sleep(1)
        prefix = archive.stat().st_size
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    endpoint = f"https://api.github.com/repos/to-mid/ios-party/actions/artifacts/{args.artifact}/zip"
    request = urllib.request.Request(endpoint, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
    })
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=30)
        raise RuntimeError("artifact API did not provide a download redirect")
    except urllib.error.HTTPError as error:
        if error.code != 302:
            raise RuntimeError(f"artifact API returned HTTP {error.code}") from None
        url = error.headers["Location"]
    # The bearer token stays in memory and is never forwarded to the blob host.
    del token
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        total = int(response.headers["Content-Length"])
        etag = response.headers["ETag"]
    chunk_size = 32 << 20
    count = (total + chunk_size - 1) // chunk_size
    done = set(range(prefix // chunk_size))
    if journal.exists():
        old = json.loads(journal.read_text())
        if old["artifact"] != args.artifact or old["bytes"] != total:
            raise RuntimeError("download journal refers to another immutable artifact")
        done = set(old["done"])
    fd = os.open(archive, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(fd, total)
    lock = threading.Lock()
    began = time.monotonic()

    def save():
        temp = journal.with_suffix(".tmp")
        temp.write_text(json.dumps({"artifact": args.artifact, "bytes": total, "done": sorted(done)}) + "\n")
        temp.replace(journal)

    def fetch(index):
        start = index * chunk_size
        end = min(total, start + chunk_size) - 1
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={
                    "Range": f"bytes={start}-{end}", "If-Match": etag, "Accept-Encoding": "identity",
                })
                with urllib.request.urlopen(req, timeout=60) as response:
                    if response.status != 206 or response.headers["Content-Range"] != f"bytes {start}-{end}/{total}":
                        raise RuntimeError("blob server returned an unexpected byte range")
                    position = start
                    while block := response.read(1 << 20):
                        if position + len(block) > end + 1:
                            raise RuntimeError("range exceeds requested length")
                        written = os.pwrite(fd, block, position)
                        if written != len(block):
                            raise RuntimeError("partial local write")
                        position += written
                    if position != end + 1:
                        raise RuntimeError("range was truncated")
                with lock:
                    done.add(index)
                    save()
                    completed = sum(min(chunk_size, total - i * chunk_size) for i in done)
                    print(f"Artifact {completed * 100 / total:.1f}% ({len(done)}/{count} ranges)", flush=True)
                return
            except (OSError, urllib.error.URLError):
                if attempt == 3:
                    raise RuntimeError(f"range {index} failed after retries") from None
        raise RuntimeError("range did not complete")

    try:
        save()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(fetch, [i for i in range(count) if i not in done]))
    finally:
        os.fsync(fd)
        os.close(fd)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "system.apfs"
    expected = "f781f6111ab3cd84a22dc2b385d2ceb51a706c16c913768e2ff3be26f2f65fa4"
    sha = hashlib.sha256()
    with zipfile.ZipFile(archive) as source:
        entries = [entry for entry in source.infolist() if entry.filename == "system.apfs"]
        if len(entries) != 1 or entries[0].file_size != 2492219392:
            raise RuntimeError("artifact is not the fixed selected system volume")
        temp = args.output / "system.apfs.partial"
        with source.open(entries[0]) as stream, temp.open("wb") as output:
            while block := stream.read(4 << 20):
                sha.update(block)
                output.write(block)
    if sha.hexdigest() != expected:
        raise RuntimeError("system volume SHA-256 differs from the verified native closure")
    temp.replace(target)
    print(f"Verified {target.resolve()} in {time.monotonic() - began:.1f}s", flush=True)


if __name__ == "__main__":
    main()
