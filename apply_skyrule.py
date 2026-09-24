#!/usr/bin/env python3
"""Pure AsianRule (Run #11) build, no sky stubs."""
from __future__ import annotations
from pathlib import Path
import re
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
    if needle not in text: die(f"anchor not found: {needle[:160]!r}")
    return text.replace(needle, addition + needle if before else needle + addition, 1)

# AsianRule patch
path = SRC / "position.h"
text = read(path)
backup(path)
text = add_once(text, "class TranspositionTable;\nstruct SharedHistories;\n",
    "extern bool ChineseRule;\nextern bool SkyRule;\nextern int  MateThreatDepth;\n")
text = add_once(text, "    Value detect_chases(int d, int ply = 0);\n    bool chase_legal(Move m) const;\n",
    "    bool has_mate_threat(Depth d = -1);\n")
write(path, text)

path = SRC / "position.cpp"
text = read(path)
backup(path)
text = add_once(text, "using namespace Attacks;\n",
    "bool ChineseRule = true;\nbool SkyRule = false;\nint  MateThreatDepth = 1;\n")

old_detect_start = "Value Position::detect_chases(int d, int ply) {"
if old_detect_start in text:
    start_idx = text.find(old_detect_start)
    end_marker = "                                               : VALUE_DRAW;\n}"
    end_idx = text.find(end_marker, start_idx) + len(end_marker)
    new_detect = r'''Value Position::detect_chases(int d, int ply) {
    Color us = sideToMove, them = ~us;
    u16 rooks[COLOR_NB] = {0xFFFF, 0xFFFF};
    u16 chase[COLOR_NB] = {0xFFFF, 0xFFFF};
    u16 newChase[COLOR_NB] = {};
    newChase[us] = chased(us);
    for (int i = 0; i < d; ++i) {
        if (!chase[~sideToMove]) {
            if (!chase[sideToMove]) break;
            std::swap(chase[sideToMove], chase[~sideToMove]);
        } else {
            if (st->checkersBB || (ChineseRule && MateThreatDepth && has_mate_threat())) {
                chase[~sideToMove] &= ChineseRule ? 0xFFFF : 0;
                rooks[~sideToMove] = 0;
                undo_move(st->move, st->capturedPiece);
                st = st->previous;
            } else {
                u16 oldChase = chased(~sideToMove);
                u16 flag = 0;
                if (!ChineseRule && rooks[~sideToMove] && (blockers_for_king(sideToMove) & pieces(sideToMove, ROOK))) {
                    Bitboard knights = pinners(~sideToMove) & pieces(~sideToMove, KNIGHT);
                    while (knights) {
                        Square s = pop_lsb(knights);
                        Bitboard b = between_bb(king_square(sideToMove), s) ^ s;
                        s = pop_lsb(b);
                        if (piece_on(s) == make_piece(sideToMove, ROOK)) flag |= 1 << idBoard[s];
                    }
                }
                undo_move(st->move, st->capturedPiece);
                st = st->previous;
                u16 chases = oldChase & ~newChase[sideToMove];
                newChase[sideToMove] = chased(sideToMove);
                if (ChineseRule) chases = oldChase & ~newChase[sideToMove];
                else if (i == d - 2) chases &= ~newChase[sideToMove];
                rooks[sideToMove] &= chases & flag;
                chase[sideToMove] &= (ChineseRule && chases) ? 0xFFFF : chases;
            }
        }
    }
    if ((!chase[us] && !chase[them]) || (rooks[us] && rooks[them])) return VALUE_DRAW;
    else if (rooks[us]) return mated_in(ply);
    else if (rooks[them]) return mate_in(ply);
    return !chase[us] ? mate_in(ply) : !chase[them] ? mated_in(ply) : VALUE_DRAW;
}

bool Position::has_mate_threat(Depth d) {
    bool mateThreat = false;
    if (d == -1) {
        StateInfo nullSt;
        do_null_move(nullSt);
        mateThreat = has_mate_threat(0);
        undo_null_move();
    } else if (d < MateThreatDepth) {
        StateInfo tempSt[2];
        for (const auto& check : MoveList<LEGAL>(*this)) {
            if (gives_check(check)) {
                do_move(check, tempSt[0]);
                bool solvable = false;
                for (const auto& evasion : MoveList<LEGAL>(*this)) {
                    do_move(evasion, tempSt[1]);
                    solvable = !has_mate_threat(d + 1);
                    undo_move(evasion);
                    if (solvable) break;
                }
                undo_move(check);
                if (!solvable) return true;
            }
        }
    }
    return mateThreat;
}'''
    text = text[:start_idx] + new_detect + text[end_idx:]
write(path, text)

path = SRC / "engine.cpp"
text = read(path)
backup(path)
text = add_once(text, '    options.add("nodestime", Option(0, 0, 10000));\n',
    '    options.add("Mate Threat Depth", Option(1, 0, 10, [](const Option& o) { MateThreatDepth = int(o); return std::nullopt; }));\n'
    '    options.add("Repetition Rule", Option("ChineseRule var AsianRule var ChineseRule", "ChineseRule", [](const Option& o) { ChineseRule = (o == "ChineseRule"); return std::nullopt; }));\n')
write(path, text)

path = SRC / "ucioption.cpp"
text = read(path)
backup(path)
text = re.sub(r'(std::string\s+token;\s*\n\s*std::istringstream ss\(defaultValue\);\s*\n\s*while \(ss >> token\)\s*\n)\s*comboMap\.add\(token, Option\(\)\);',
    r'''\1        { if (token == "var" || comboMap.count(token)) continue; comboMap.add(token, Option()); }''', text, count=1)
write(path, text)

print("Pure AsianRule build applied.")
