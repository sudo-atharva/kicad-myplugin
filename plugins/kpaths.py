"""Find KiCad / FreeCAD / Java / tool paths on any machine (Windows, macOS, Linux). No hard-coded user paths.

Order everywhere: explicit env var -> what the running KiCad tells us (its env vars) -> PATH -> standard install
locations. Results are cached per process.
"""
import functools, glob, os, re, shutil, subprocess, sys

import pcbnew

WIN, MAC = os.name == "nt", sys.platform == "darwin"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _first(paths):
    for p in paths:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return None


def _kicad_env(suffix):
    """Value of KICADxx_<suffix> (e.g. KICAD10_FOOTPRINT_DIR) - KiCad sets these for plugins it runs."""
    for k in sorted(os.environ, reverse=True):  # newest version first
        if re.fullmatch(r"KICAD\d+_" + suffix, k) and os.path.isdir(os.environ[k]):
            return os.environ[k]
    return None


@functools.lru_cache(None)
def kicad_bin():
    """Folder holding kicad-cli (and on Windows python.exe)."""
    here = os.path.dirname(os.path.abspath(pcbnew.__file__))
    cands = [os.path.join(here, "..", ".."),                       # Windows: bin/Lib/site-packages
             os.path.dirname(sys.executable)]
    if MAC:
        cands += ["/Applications/KiCad/KiCad.app/Contents/MacOS"]
    which = shutil.which("kicad-cli")
    if which:
        cands.insert(0, os.path.dirname(which))
    for c in cands:
        if _first([os.path.join(c, "kicad-cli.exe" if WIN else "kicad-cli")]):
            return os.path.abspath(c)
    return os.path.abspath(cands[0])


@functools.lru_cache(None)
def kicad_cli():
    return _first([os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"),
                   os.path.join(kicad_bin(), "kicad-cli.exe" if WIN else "kicad-cli"),
                   "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli", "/usr/bin/kicad-cli",
                   "/usr/local/bin/kicad-cli"]) or "kicad-cli"


@functools.lru_cache(None)
def kicad_python():
    """A Python that can import pcbnew (for helper subprocesses). Inside KiCad's GUI sys.executable is kicad.exe."""
    if os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    return _first([os.path.join(kicad_bin(), "python.exe"), os.path.join(kicad_bin(), "pythonw.exe"),
                   os.path.join(kicad_bin(), "python3"),
                   "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
                   shutil.which("python3"), shutil.which("python")]) or "python"


def _share():
    here = os.path.dirname(os.path.abspath(pcbnew.__file__))
    return _first([os.path.join(here, "..", "..", "..", "share", "kicad"),        # Windows install
                   "/Applications/KiCad/KiCad.app/Contents/SharedSupport",          # macOS
                   "/usr/share/kicad", "/usr/local/share/kicad"])                   # Linux


@functools.lru_cache(None)
def footprint_dir():
    s = _share()
    return _kicad_env("FOOTPRINT_DIR") or _first([s and os.path.join(s, "footprints")])


@functools.lru_cache(None)
def symbol_dir():
    s = _share()
    return _kicad_env("SYMBOL_DIR") or _first([s and os.path.join(s, "symbols")])


@functools.lru_cache(None)
def model_dir():
    s = _share()
    return _kicad_env("3DMODEL_DIR") or _first([s and os.path.join(s, "3dmodels")])


def documents_dir():
    """The real Documents folder (follows OneDrive / folder redirection on Windows)."""
    if WIN:
        try:
            import ctypes
            from ctypes import wintypes
            guid = (ctypes.c_byte * 16).from_buffer_copy(
                b"\xd0\x9a\xd3\xfd\x8f\x23\xaf\x46\xad\xb4\x6c\x85\x48\x03\x69\xc7")  # FOLDERID_Documents
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def user_data_dir():
    """Per-user writable folder for MyPlugin downloads/caches (never the plugin folder)."""
    if WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "MyPlugin")
    os.makedirs(d, exist_ok=True)
    return d


def java():
    jh = os.environ.get("JAVA_HOME")
    return _first([shutil.which("java"), jh and os.path.join(jh, "bin", "java.exe" if WIN else "java")]
                  + (glob.glob(r"C:\Program Files\*\jdk*\bin\java.exe") + glob.glob(r"C:\Program Files\*\jre*\bin\java.exe")
                     if WIN else []))


def freecad():
    if os.environ.get("FREECAD_EXE") and os.path.exists(os.environ["FREECAD_EXE"]):
        return os.environ["FREECAD_EXE"]
    found = [shutil.which("freecad"), shutil.which("FreeCAD")]
    if WIN:
        for r in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                  os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
            if r:
                found += sorted(glob.glob(os.path.join(r, "FreeCAD*", "bin", "freecad.exe")), reverse=True)
    elif MAC:
        found += ["/Applications/FreeCAD.app/Contents/MacOS/FreeCAD"]
    else:
        found += ["/usr/bin/freecad", "/usr/local/bin/freecad", "/snap/bin/freecad"]
    return _first(found)


def freecad_user_dirs():
    """FreeCAD per-user data roots (Mod folders live inside, possibly under a version folder like v1-1)."""
    if WIN:
        return [os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FreeCAD")]
    if MAC:
        return [os.path.join(os.path.expanduser("~"), "Library", "Application Support", "FreeCAD")]
    return [os.path.join(os.path.expanduser("~"), ".local", "share", "FreeCAD"), os.path.join(os.path.expanduser("~"), ".FreeCAD")]


def pid_alive(pid):
    if WIN:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def py_has(module):
    """Can KiCad's helper Python import `module`?"""
    r = subprocess.run([kicad_python(), "-c", "import " + module], capture_output=True, creationflags=NOWIN)
    return r.returncode == 0


def pip_install(*packages):
    """pip --user into KiCad's Python (lands in KiCad's own 3rd-party site-packages on Windows)."""
    r = subprocess.run([kicad_python(), "-m", "pip", "install", "--user", "--disable-pip-version-check"] + list(packages),
                       capture_output=True, text=True, creationflags=NOWIN)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def report():
    return {"kicad_bin": kicad_bin(), "kicad_cli": kicad_cli(), "kicad_python": kicad_python(),
            "footprints": footprint_dir(), "symbols": symbol_dir(), "3dmodels": model_dir(),
            "documents": documents_dir(), "user_data": user_data_dir(), "java": java(), "freecad": freecad()}


if __name__ == "__main__":
    for k, v in report().items():
        print("%-13s %s" % (k, v))
