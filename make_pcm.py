"""Write the KiCad Plugin and Content Manager repository for MyPlugin (pcm/), from dist/MyPlugin-<version>.zip.

Users add this URL once in KiCad > Plugin and Content Manager > Manage... (repositories):
    https://raw.githubusercontent.com/sudo-atharva/kicad-myplugin/main/pcm/repository.json
then MyPlugin shows up there and KiCad offers each new version as an update.

Order for a release: python build.py, python make_pcm.py, commit + push, then upload the SAME zip to the GitHub
release v<version> (the checksum in pcm/packages.json must match it).
"""
import hashlib, json, os, time, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "sudo-atharva/kicad-myplugin"
RAW = "https://raw.githubusercontent.com/%s/main/pcm/" % REPO
meta = json.load(open(os.path.join(ROOT, "metadata.json"), encoding="utf-8"))
ver = meta["versions"][0]["version"]
zf = os.path.join(ROOT, "dist", "MyPlugin-%s.zip" % ver)
data = open(zf, "rb").read()
install = sum(i.file_size for i in zipfile.ZipFile(zf).infolist() if i.filename != "metadata.json")
now = int(time.time())
stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
out = os.path.join(ROOT, "pcm")
os.makedirs(out, exist_ok=True)


def sha(b):
    return hashlib.sha256(b).hexdigest()


pkg = dict(meta)
pkg.pop("$schema", None)
pkg["versions"] = [dict(meta["versions"][0], download_url="https://github.com/%s/releases/download/v%s/MyPlugin-%s.zip" % (REPO, ver, ver),
                        download_sha256=sha(data), download_size=len(data), install_size=install)]
packages = json.dumps({"packages": [pkg]}, indent=2, ensure_ascii=False).encode("utf-8")
open(os.path.join(out, "packages.json"), "wb").write(packages)

# icon shown in the Plugin Manager list: resources.zip with <identifier>/icon.png
res = os.path.join(out, "resources.zip")
with zipfile.ZipFile(res, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(ROOT, "resources", "icon.png"), "%s/icon.png" % meta["identifier"])
resdata = open(res, "rb").read()

repo = {
    "$schema": "https://go.kicad.org/pcm/schemas/v1#/definitions/Repository",
    "name": "MyPlugin by sudo-atharva",
    "maintainer": meta["author"],
    "packages": {"url": RAW + "packages.json", "sha256": sha(packages), "update_time_utc": stamp, "update_timestamp": now},
    "resources": {"url": RAW + "resources.zip", "sha256": sha(resdata), "update_time_utc": stamp, "update_timestamp": now},
}
open(os.path.join(out, "repository.json"), "w", encoding="utf-8", newline="\n").write(json.dumps(repo, indent=2) + "\n")
print("pcm/ written for", ver, "zip sha256", sha(data))
