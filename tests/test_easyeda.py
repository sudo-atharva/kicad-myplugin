"""EasyEDA import self-check without internet: what convert() does after easyeda2kicad ran (library tables,
3D paths) and placing the part. Run with KiCad's Python:  python tests/test_easyeda.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins"))
import pcbnew, kpaths, easyeda_import as ee

d = tempfile.mkdtemp()
board_file = os.path.join(d, "t.kicad_pcb")
pcbnew.SaveBoard(board_file, pcbnew.BOARD())
pretty = ee.lib_base(board_file) + ".pretty"
os.makedirs(pretty)
# what easyeda2kicad writes: a footprint with an absolute 3D model path
src = os.path.join(kpaths.footprint_dir(), "Package_SO.pretty", "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod")
mod = os.path.join(pretty, "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL.kicad_mod")
text = open(src, encoding="utf-8").read()
text = text.replace(text[text.find("(model"):text.find("\n", text.find("(model"))],
                    '(model "C:/Users/someone/AppData/easyeda.3dshapes/SOIC-8_L4.9.wrl"')
open(mod, "w", encoding="utf-8").write(text)
ee.fix_models(mod)
assert '(model "${KIPRJMOD}/libs/easyeda.3dshapes/SOIC-8_L4.9.wrl"' in open(mod, encoding="utf-8").read()

# library tables: created, then extended, never duplicated
fpt = os.path.join(d, "fp-lib-table")
assert ee.add_lib(fpt, "fp", "${KIPRJMOD}/libs/easyeda.pretty")
assert not ee.add_lib(fpt, "fp", "${KIPRJMOD}/libs/easyeda.pretty")
open(os.path.join(d, "sym-lib-table"), "w").write('(sym_lib_table\n  (version 7)\n  (lib (name "Mine")(type "KiCad")(uri "x.kicad_sym")(options "")(descr ""))\n)\n')
assert ee.add_lib(os.path.join(d, "sym-lib-table"), "sym", "${KIPRJMOD}/libs/easyeda.kicad_sym")
sym = open(os.path.join(d, "sym-lib-table")).read()
assert sym.count("(lib ") == 2 and sym.rstrip().endswith(")") and '"Mine"' in sym, sym

# place: library EasyEDA, LCSC field for the BOM, model path kept
b = pcbnew.LoadBoard(board_file)
fp = ee.place(b, board_file, "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL", "C2040")
assert fp.GetFPID().GetLibNickname() == "EasyEDA" and fp.GetFieldText("LCSC") == "C2040", (fp.GetFPIDAsString(), fp.GetFieldText("LCSC"))
assert "${KIPRJMOD}/libs/easyeda.3dshapes/" in fp.Models()[0].m_Filename
pcbnew.SaveBoard(board_file, b)
sys.path.insert(0, os.path.dirname(ee.__file__))
import bom_export
bom = repr(bom_export.collect(pcbnew.LoadBoard(board_file)))
assert "C2040" in bom, bom  # the BOM's LCSC column gets it
print("easyeda ok")
