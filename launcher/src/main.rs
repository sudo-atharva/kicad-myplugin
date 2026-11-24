//! "Project Manager.exe": starts project_manager.py (next to this exe) with KiCad's own Python, without a console.
//! KiCad's Python is found through KICAD_PYTHON, else the standard install folders (newest KiCad first).
#![windows_subsystem = "windows"]

use std::path::{Path, PathBuf};
use std::process::Command;

fn kicad_python() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("KICAD_PYTHON").map(PathBuf::from).filter(|p| p.is_file()) {
        return Some(p);
    }
    let roots: Vec<PathBuf> = ["ProgramFiles", "ProgramW6432", "LOCALAPPDATA"]
        .iter()
        .filter_map(|v| std::env::var_os(v).map(PathBuf::from))
        .flat_map(|r| [r.join("KiCad"), r.join("Programs").join("KiCad")])
        .collect();
    for ver in ["10.0", "10.99", "9.0"] {
        for r in &roots {
            let p = r.join(ver).join("bin").join("pythonw.exe");
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

#[cfg(windows)]
fn message(text: &str) {
    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(hwnd: *mut u8, text: *const u16, caption: *const u16, kind: u32) -> i32;
    }
    let w = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    unsafe { MessageBoxW(std::ptr::null_mut(), w(text).as_ptr(), w("Project Manager").as_ptr(), 0x30) };
}

#[cfg(not(windows))]
fn message(text: &str) {
    eprintln!("{text}");
}

fn main() {
    let exe = std::env::current_exe().unwrap_or_default();
    let dir = exe.parent().unwrap_or(Path::new("."));
    let script = dir.join("project_manager.py");
    if !script.is_file() {
        return message(&format!("project_manager.py is missing next to this program:\n{}", dir.display()));
    }
    let Some(py) = kicad_python() else {
        return message("KiCad 10 was not found. Install KiCad 10, or set KICAD_PYTHON to its pythonw.exe.");
    };
    if let Err(e) = Command::new(&py).arg(&script).args(std::env::args_os().skip(1)).current_dir(dir).spawn() {
        message(&format!("Could not start {}:\n{e}", py.display()));
    }
}
