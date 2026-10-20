"""Build the KiCad PCM package: dist/MyPlugin-<version>.zip (metadata.json + plugins/ + resources/).
Prints sha256/size for the official metadata repository submission."""
import hashlib, json, os, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
meta = json.load(open(os.path.join(ROOT, "metadata.json"), encoding="utf-8"))
version = meta["versions"][0]["version"]
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", "MyPlugin-%s.zip" % version)

install_size = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(ROOT, "metadata.json"), "metadata.json")
    for top in ("plugins", "resources"):
        for d, _, files in os.walk(os.path.join(ROOT, top)):
            if "__pycache__" in d:
                continue
            for f in files:
                if f.endswith((".pyc", "fp_index.json", "freerouting.jar")):
                    continue
                p = os.path.join(d, f)
                z.write(p, os.path.relpath(p, ROOT).replace(os.sep, "/"))
                install_size += os.path.getsize(p)

data = open(out, "rb").read()
print(out)
print("download_sha256:", hashlib.sha256(data).hexdigest())
print("download_size:  ", len(data))
print("install_size:   ", install_size)
