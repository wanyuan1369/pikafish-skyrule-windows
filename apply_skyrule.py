#!/usr/bin/env python3
"""SkyRule overlay: keep official loop detection, only patch result values."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

def die(msg): raise SystemExit(f"Patch aborted: {msg}")
def read(p):
    if not p.exists(): die(f"missing {p}")
    return p.read_text(encoding="utf-8")
def write(p, t): p.write_text(t, encoding="utf-8")
def backup(p):
    b = p.with_suffix(p.suffix + ".pre-patch")
    if not b.exists(): shutil.copy2(p, b)
def add_once(text, needle, addition, before=False):
    if addition in text: return text
    if needle not in text: die(f"anchor not found: {needle[:100]!r}")
    return text.replace(needle, addition + needle if before else needle + addition, 1)

# 1. SkyRule header
header = ROOT / "src_skyrule.h"
write(SRC / "skyrule.h", header.read_text(encoding="utf-8"))

# 2. position.cpp: include header
p = SRC / "position.cpp"
t = read(p)
backup(p)
if '#include "skyrule.h"' not in t:
    t = add_once(t, '#include "position.h"\n', '#include "skyrule.h"\n')

# 3. Patch rule_judge: replace all violation scores with ±24999
old = """                // 3 folds and 2 fold draws can be judged immediately
                if (result == VALUE_DRAW || cnt == 2)
                    return true;"""
new = """                // ===== SKY RULE: convert rule violation scores to ±24999 =====
                if (result > VALUE_MATE / 2)
                    result = SKY_RULE_WIN;
                else if (result < -VALUE_MATE / 2)
                    result = SKY_RULE_LOSS;
                // ===== END SKY RULE =====

                // 3 folds and 2 fold draws can be judged immediately
                if (result == VALUE_DRAW || cnt == 2)
                    return true;"""
if old in t:
    t = t.replace(old, new, 1)
else:
    die("Could not find rule_judge result anchor")

write(p, t)
print("SkyRule patch applied.")
