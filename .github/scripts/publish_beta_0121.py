"""Publish an already verified distribution from within a GitHub-hosted runner."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = "FourthQuadrantInc/HelloOnce-Public-Beta"
TAG = "v0.1.21"
OUT = Path("distribution")
EXPECTED = {
    "0.1.21.md", "install.py", "release.json", "SHA256SUMS", "helloonce-android-v2.0.0.apk",
    "CONNECTORS.md", "LANGUAGE.md", "MOBILE-VOICE.md", "SOURCE-API.md", "UPDATES.md",
    *(f"{program}-{TAG}-{platform}" for program in ("helloonce", "openworld-memory")
      for platform in ("macos-arm64.tar.gz",)),
}
def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)

def anonymous_digest(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                return "sha256:" + hashlib.file_digest(response, "sha256").hexdigest()
        except (urllib.error.HTTPError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(5)

def verify():
    url = os.environ["BUNDLE_URL"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(
            (".blob.core.windows.net", ".githubusercontent.com", ".actions.githubusercontent.com")):
        raise ValueError("Expected a GitHub artifact storage URL")
    # Keep the short-lived URL out of output, including HTTP exception messages.
    try:
        with urllib.request.urlopen(url, timeout=90) as response, Path("staged.zip").open("wb") as dest:
            shutil.copyfileobj(response, dest)
    except OSError:
        raise RuntimeError("GitHub artifact download failed; refresh the staged download URL") from None
    trusted = Path(".github/staged-beta-0121.sha256").read_text().strip()
    if digest(Path("staged.zip")) != trusted:
        raise ValueError("Distribution differs from the staged GitHub artifact")
    OUT.mkdir()
    with zipfile.ZipFile("staged.zip") as archive:
        if sorted(archive.namelist()) != sorted(EXPECTED):
            raise ValueError("Unexpected release files")
        for item in archive.infolist():
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Unexpected symbolic link")
            with archive.open(item) as src, (OUT / item.filename).open("xb") as dest:
                shutil.copyfileobj(src, dest)
    sums = "".join(f"{digest(OUT / name)}  {name}\n" for name in sorted(EXPECTED - {"SHA256SUMS"}))
    if (OUT / "SHA256SUMS").read_text() != sums:
        raise ValueError("Invalid release checksums")
    if json.loads((OUT / "release.json").read_text()) != {"schema": 1, "version": "0.1.21", "revision": 1}:
        raise ValueError("Unexpected release version")

def publish():
    wanted = {name: "sha256:" + digest(OUT / name) for name in EXPECTED}
    fields = "isDraft,isPrerelease,assets,url"
    def current():
        return json.loads(gh("release", "view", TAG, "--repo", REPO, "--json", fields))
    releases = json.loads(gh("release", "list", "--repo", REPO, "--limit", "100", "--json", "tagName"))
    if all(entry["tagName"] != TAG for entry in releases):
        latest = json.loads(gh("release", "view", "--repo", REPO, "--json", "tagName"))["tagName"]
        if latest != "v0.1.20":
            raise RuntimeError(f"Latest changed to {latest}; review before publishing")
        private_notes = Path("release-notes.md")
        changelog = (OUT / "0.1.21.md").read_text().split("## Updating / 更新", 1)[0]
        private_notes.write_text("**macOS (Apple Silicon) preview.** Download the attached installer and pass "
                                 + "`--version v0.1.21`. The automatic update channel for macOS and Windows is v0.1.20.\n\n"
                                 + "**本次预览适用于 Apple 芯片 Mac。** 请使用本页的 `install.py` 并加上 "
                                 + "`--version v0.1.21`。Mac 和 Windows 的自动更新仍指向 v0.1.20。\n\n" + changelog)
        gh("release", "create", TAG, "--repo", REPO, "--draft", "--target", "main", "--title",
           "HelloOnce 0.1.21 (macOS preview)", "--notes-file", str(private_notes))
    release = current()
    existing = {asset["name"]: asset["digest"] for asset in release["assets"]}
    if any(wanted.get(name) != value for name, value in existing.items()):
        raise RuntimeError("A release asset has different contents. Review before publishing.")
    missing = sorted(EXPECTED - existing.keys())
    if missing:
        if not release["isDraft"]:
            raise RuntimeError("A partial release is already public. Review before modifying it.")
        gh("release", "upload", TAG, "--repo", REPO, *(str(OUT / name) for name in missing))
    release = current()
    if {asset["name"]: asset["digest"] for asset in release["assets"]} != wanted:
        raise RuntimeError("Uploaded assets differ from the staged distribution")
    if release["isDraft"]:
        print(f"Verified all {len(wanted)} attachments in the macOS draft.")
        return
    for name in sorted(EXPECTED):
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{name}"
        if anonymous_digest(url) != wanted[name]:
            raise ValueError(f"Anonymous download mismatch for {name}")
    print(f"Verified anonymous downloads for all {len(wanted)} assets. {release['url']}")

if __name__ == "__main__":
    {"verify": verify, "publish": publish}[sys.argv[1]]()
