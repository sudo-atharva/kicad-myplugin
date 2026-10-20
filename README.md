# MyPlugin for KiCad 10

One toolbar button opens a hub with twelve tools that take care of the tedious parts of PCB design: fab-correct DRC, trace widths from real current, datasheet-style wide copper, power pours and via stitching, a horizontal/vertical autorouter, clean silkscreen, documentation drawings, a live KiCad ⇄ FreeCAD link, and Eagle-import cleanup.

![MyPlugin hub](docs/hub.png)

## Tools

| Tool | What it does |
|---|---|
| **Fab Rules** | Sets Board Setup constraints, the Default netclass and DRC rules to Lion Circuits' published capabilities (MII / Custom / Rush, 1–3 oz). Optionally makes them the default for every new project. |
| **Net Setup** | Enter voltage and current per net → IPC-2221 trace width, clearance, via size and via count. Creates netclasses, adds the sizes to the toolbar dropdowns, and writes `.kicad_dru` rules so DRC flags power traces that are too thin. |
| **Power Trace** | Select a pad or trace, enter V/A: the net gets its netclass, existing traces are widened, and the router switches to netclass width. |
| **Copper Path** | Wide copper that behaves like a trace. Route the net normally (click the points it must touch), pick a width (3 mm, 5 mm…) and 45° or round corners: the net's traces on that layer get wrapped in a zone of that width. It keeps clearance to every other net by itself and narrows into pin rows instead of spreading over neighbouring pins. Drag the traces, press **Refresh Copper Paths** (own toolbar button), and the copper re-forms. Good for copying datasheet layouts. |
| **Power Pours** | Copper around regulator pad groups, GND pours, and plane vias for pads a pour would cut off. Skips pours that would create islands and says why. |
| **Via Stitching** | [ViaStitcher](https://github.com/weirdgyn/viastitcher) by Michele Santucci (bundled, MIT): select a zone, fill it with a configurable stitching-via grid. |
| **Autoroute H/V** | Freerouting with horizontal-on-top / vertical-on-bottom preference. Uses your netclasses; fanout and neck-down are off, so it never shrinks a trace to make it fit. |
| **Silk Beautify** | Places references cleanly, prints net names next to connector pins, moves values to Fab, lists anything it couldn't place. |
| **Docs (PcbDraw)** | [PcbDraw](https://github.com/yaqwsx/PcbDraw) by Jan Mrázek: front/back drawings (SVG) and 3D renders (PNG) into the project's `04_Docs/` (or `docs/` next to the board). Offers to install PcbDraw if missing. |
| **FreeCAD (StepUp)** | Opens the board in FreeCAD with [KiCad StepUp](https://github.com/easyw/kicadStepUpMod) already loaded: no workbench clicking, no file picking. FreeCAD reloads by itself whenever you save in KiCad; its **KiCad Bridge** panel pushes part moves / outline back and makes KiCad reload. Neither app has to be closed. |
| **3D Models** | For footprints without a 3D model (typical after an Eagle import), finds the matching KiCad library footprint by pad geometry and attaches its model, aligned. |
| **Eagle → KiCad Symbols** | Replaces imported Eagle schematic symbols with KiCad library symbols, keeping the Eagle pin numbers so the PCB still matches. Nothing is saved unless the netlist is identical. |

Example: LM2596 buck converter placed with Fab Rules + Net Setup applied:

![example](docs/example_lm2596_placement.png)

## Install

**From the release zip (recommended):** download `MyPlugin-<version>.zip` from [Releases](../../releases) → KiCad → *Plugin and Content Manager* → **Install from File…** → pick the zip → restart KiCad.

**Manual:** copy the `plugins/` folder to `<KiCad documents>/10.0/scripting/plugins/myplugin/` and restart KiCad.

Requirements: **KiCad 10.0** with its libraries. Nothing is hard-coded to one PC: KiCad, its libraries, `kicad-cli`, Java and FreeCAD are found through KiCad's own variables, `PATH` and the standard install locations (Windows, macOS, Linux; the Documents folder follows OneDrive/redirection). What a tool needs but the machine lacks is offered as a one-click install into a per-user folder:

| Tool | Needs | If missing |
|---|---|---|
| Autoroute H/V | Java (current, e.g. Temurin 25) + Freerouting | Freerouting: downloaded from its official releases. Java: link to install it. |
| Docs (PcbDraw) | PcbDraw | `pip install --user pcbdraw` into KiCad's Python |
| FreeCAD (StepUp) | FreeCAD 0.21+ (tested 1.1), KiCad StepUp, kicad-python | StepUp downloaded into the FreeCAD user `Mod` folder FreeCAD reads; kicad-python pip-installed. Push/pull also needs *Preferences › Plugins › Enable KiCad API* (off by default). |

Overrides if you keep tools elsewhere: `KICAD_CLI`, `FREEROUTING_JAR`, `FREECAD_EXE`, `JAVA_HOME`.

## Where files go

Nothing is written into the plugin folder. Everything a tool produces goes **next to your board**: `.kicad_dru` rules, Net Setup data, generated 3D boxes, the mapped symbol library. If the board sits in a project laid out like below, docs go to `04_Docs/` and FreeCAD files to `07_FreeCAD/`. The shared 3D-model library index is cached in `%LOCALAPPDATA%\MyPlugin\`.

```
Project-Name/
├── 01_PCB/            ← KiCad project
├── 02_Datasheets/
├── 03_Manufacturing/
├── 04_Docs/           ← Docs (PcbDraw) output
├── 05_Testing/
├── 06_Code/
└── 07_FreeCAD/        ← FreeCAD (StepUp) files
```

## Suggested workflow

Fab Rules → Net Setup → place parts → Power Pours / Copper Path → Via Stitching → Autoroute H/V / Power Trace → Silk Beautify → DRC → Docs.

## Extras in `docs/`

- **`KiCad-OneHand-Guide.pdf`**: printable guide: one-hand keyboard layout, MyPlugin workflow, design blocks how-to, Eagle ⇄ KiCad conversion.
- **`onehand.hotkeys`**: the keyboard layout itself (same key = same idea in schematic and PCB: `W` wire/route, `V` global label/via, `D` drag, `F` mirror/flip, `X` delete…). Import via *Preferences › Hotkeys › Import Hotkeys…*.

## Command line

Every tool also runs headless with KiCad's Python, e.g.

```
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\fab_rules.py board.kicad_pcb --service MII --oz 1
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\net_setup.py board.kicad_pcb --set "+12V=12:3" --set "GND=0:3"
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\copper_path.py board.kicad_pcb --net VIN --layer F.Cu --width 5 --style 45
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\copper_path.py board.kicad_pcb --refresh
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\docs_pcbdraw.py board.kicad_pcb
"C:\Program Files\KiCad\10.0\bin\python.exe" plugins\eagle3d.py board.brd
```

Tests: `python tests/test_eagle3d.py` (3D model placement against KiCad's own footprints, all rotations).

## Notes & limits

- Net Setup widths use the IPC-2221 formula (slightly conservative vs IPC-2152); clearance from IPC-2221 table B2.
- Fab Rules numbers come from [Lion Circuits' capabilities page](https://lioncircuits.com/pcb-manufacturing-capabilities); check them against your order.
- Copper Path: one path per net per layer (all of that net's traces on the layer form the skeleton). Copper narrower than 0.6 mm (fingers between pins) is removed.
- Custom rules written by hand in `.kicad_dru` are not passed to Freerouting; netclass values are.
- There is no proper automatic KiCad → Eagle conversion; see the PDF for options.

## Credits & licenses

- MyPlugin: MIT © Atharva Tikle.
- `plugins/viastitcher/`: ViaStitcher © Michele Santucci, MIT (see `plugins/viastitcher/LICENSE`). Only change: it doesn't register its own toolbar button.
- PcbDraw (MIT, Jan Mrázek) and KiCad StepUp (AGPL-3.0, Maurice "easyw") are **not** bundled; they are installed separately and called as external tools.
- Freerouting (GPL-3.0) is not bundled.
