#!/usr/bin/env python3
"""SkyRule: keep official loop detection, replace violation scores with ±24999"""
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

# SkyRule header
write(SRC / "skyrule.h", '''#pragma once
constexpr int SKY_RULE_WIN = 24999;
constexpr int SKY_RULE_LOSS = -24999;
''')

# Patch position.cpp
p = SRC / "position.cpp"
t = read(p)
backup(p)

# Include skyrule.h
if '#include "skyrule.h"' not in t:
    t = t.replace('#include "position.h"\n', '#include "position.h"\n#include "skyrule.h"\n', 1)

# Patch rule_judge result: replace violation scores with ±24999
# Exact anchor from official source:
old = """                // 3 folds and 2 fold draws can be judged immediately
                if (result == VALUE_DRAW || cnt == 2)
                    return true;"""
new = """                // ===== SKY RULE: convert rule violation scores to ±24999 =====
                if (result > VALUE_MATE / 2)
                    result = Value(SKY_RULE_WIN);
                else if (result < -VALUE_MATE / 2)
                    result = Value(SKY_RULE_LOSS);
                // ===== END SKY RULE =====

                // 3 folds and 2 fold draws can be judged immediately
                if (result == VALUE_DRAW || cnt == 2)
                    return true;"""

if old in t:
    t = t.replace(old, new, 1)
else:
    die("Could not find rule_judge anchor in official source")

write(p, t)
print("SkyRule minimal patch applied successfully.")
