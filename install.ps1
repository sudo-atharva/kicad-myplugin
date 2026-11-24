# MyPlugin one-line installer for KiCad 10 on Windows. In PowerShell:
#   irm https://raw.githubusercontent.com/sudo-atharva/kicad-myplugin/main/install.ps1 | iex
# Downloads the latest release, puts it where KiCad's Plugin Manager puts it (Documents\KiCad\10.0\3rdparty),
# and offers a desktop + Start menu shortcut for the Project Manager. Run it again to update.
$ErrorActionPreference = 'Stop'
$repo = 'sudo-atharva/kicad-myplugin'
$id = 'com_github_sudo-atharva_myplugin'

Write-Host 'Looking for the latest MyPlugin release...'
$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'MyPlugin-installer' }
$asset = $rel.assets | Where-Object { $_.name -like 'MyPlugin-*.zip' } | Select-Object -First 1
if (-not $asset) { throw "No MyPlugin zip on the latest release ($($rel.tag_name))." }

# KiCad's 3rd-party folder: its own variable if set, else Documents\KiCad\10.0\3rdparty (follows OneDrive)
$third = $env:KICAD10_3RD_PARTY
if (-not $third) { $third = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'KiCad\10.0\3rdparty' }
$plugins = Join-Path $third "plugins\$id"
$resources = Join-Path $third "resources\$id"

$tmp = Join-Path $env:TEMP ("myplugin-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp $asset.name
Write-Host "Downloading $($asset.name)..."
Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
Expand-Archive $zip (Join-Path $tmp 'x')

if (Get-Process -Name kicad, pcbnew -ErrorAction SilentlyContinue) {
    Write-Host 'KiCad is running: the update takes effect after you restart it.' -ForegroundColor Yellow
}
foreach ($pair in @(@('plugins', $plugins), @('resources', $resources))) {
    $src = Join-Path $tmp "x\$($pair[0])"
    if (Test-Path $src) {
        New-Item -ItemType Directory -Force $pair[1] | Out-Null
        Copy-Item (Join-Path $src '*') $pair[1] -Recurse -Force
    }
}
Copy-Item (Join-Path $tmp 'x\metadata.json') $plugins -Force -ErrorAction SilentlyContinue
Remove-Item $tmp -Recurse -Force

$exe = Join-Path $plugins 'Project Manager.exe'
Write-Host "Installed $($rel.tag_name) into $plugins" -ForegroundColor Green
if ((Test-Path $exe) -and ((Read-Host 'Project Manager shortcut on the desktop and in the Start menu? [Y/n]') -notmatch '^[nN]')) {
    $sh = New-Object -ComObject WScript.Shell
    foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $l = $sh.CreateShortcut((Join-Path $d 'Project Manager (KiCad).lnk'))
        $l.TargetPath = $exe; $l.WorkingDirectory = $plugins
        $l.Description = 'MyPlugin Project Manager: boards, versions, git'; $l.Save()
    }
    Write-Host 'Shortcuts made.'
}
Write-Host 'Restart KiCad: the MyPlugin button is in the PCB editor toolbar.'
