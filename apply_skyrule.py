#!/usr/bin/env python3
"""Apply a deterministic SkyRule overlay to an official Pikafish checkout.

The overlay is intentionally fail-fast.  It is designed for a GitHub Action:
checkout official-pikafish/Pikafish, run this script from this overlay, then
build the modified src tree.  The script refuses to patch an unrecognized
position/search implementation.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def die(msg: str) -> None:
    raise SystemExit(f"SkyRule patch aborted: {msg}")


def read(path: Path) -> str:
    if not path.exists():
        die(f"missing {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".pre-skyrule")
    if not bak.exists():
        shutil.copy2(path, bak)


def add_once(text: str, needle: str, addition: str, *, before: bool = False) -> str:
    if addition in text:
        return text
    if needle not in text:
        die(f"anchor not found: {needle[:160]!r}")
    return text.replace(needle, addition + needle if before else needle + addition, 1)


# The build is a dedicated SkyRule binary: no runtime ambiguity.
header = ROOT / "src_skyrule.h"
write(SRC / "skyrule.h", header.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# position.h
# ---------------------------------------------------------------------------
path = SRC / "position.h"
text = read(path)
backup(path)
text = add_once(
    text,
    "    bool  rule_judge(Value& result, int ply = 0);\n",
    "    bool  sky_rule_judge(Value& result, int ply = 0);\n",
)

# Private helpers.  Keeping these as Position members gives them legitimate
# access to the existing idBoard and detect_chases machinery.
private_anchor = "    Value                 detect_chases(int d, int ply = 0);\n"
private_add = (
    "    bool sky_check_limit_violation(Color& violator) const;\n"
    "    bool sky_chase_limit_violation(Color& violator) const;\n"
    "    bool sky_classify_twofold(int d, Value& result, int ply);\n"
)
text = add_once(text, private_anchor, private_add)
write(path, text)

# ---------------------------------------------------------------------------
# position.cpp
# ---------------------------------------------------------------------------
path = SRC / "position.cpp"
text = read(path)
backup(path)
if '#include "skyrule.h"' not in text:
    text = add_once(text, '#include "position.h"\n', '#include "skyrule.h"\n')

marker = "// Tests whether the position may end the game by rule 60, insufficient material, draw repetition,"
if "bool Position::sky_rule_judge(Value& result, int ply)" not in text:
    if marker not in text:
        die("position.cpp rule_judge marker not found")

    helper = r'''
namespace {

inline Value sky_rule_result(Color violator, Color sideToMove) {
    return violator == sideToMove ? SKY_RULE_LOSS : SKY_RULE_WIN;
}

inline Value sky_rule_from_chase_result(Value v) {
    if (v > VALUE_DRAW)
        return SKY_RULE_WIN;
    if (v < VALUE_DRAW)
        return SKY_RULE_LOSS;
    return VALUE_DRAW;
}

inline int bit_count(uint32_t x) {
    int n = 0;
    while (x)
    {
        x &= x - 1;
        ++n;
    }
    return n;
}

}  // namespace

// TianTian practical long-check limit:
//   one checking piece  -> 6 non-forced checks
//   two checking pieces -> 12 non-forced checks
//   three+              -> 18 non-forced checks
// A check made while the mover is already in check is forced and does not
// advance the count.  A capture resets the sequence.  The violating move is
// judged in its child position, so its parent can proactively avoid it.
bool Position::sky_check_limit_violation(Color& violator) const {
    Position rollback;
    std::memcpy((void*) &rollback, (const void*) this, offsetof(Position, filter));

    int idAt[SQUARE_NB];
    for (int& x : idAt)
        x = -1;

    int nextId[COLOR_NB] = {0, 0};
    for (Square s = SQ_A0; s <= SQ_I9; ++s)
        if (rollback.piece_on(s) != NO_PIECE)
        {
            Color c = color_of(rollback.piece_on(s));
            if (nextId[c] < 31)
                idAt[s] = nextId[c]++;
        }

    StateInfo* stp = this->st;
    int      run[COLOR_NB]  = {0, 0};
    uint32_t seen[COLOR_NB] = {0, 0};

    for (int n = 0; stp && n < 40; ++n)
    {
        const Move m = stp->move;
        if (m.null())
            break;

        const Color mover = ~rollback.side_to_move();
        const Piece moved = rollback.piece_on(m.to_sq());
        const int id = idAt[m.to_sq()];
        const bool isCheck = bool(stp->checkersBB);
        const bool forced  = stp->previous && bool(stp->previous->checkersBB);
        const bool capture = stp->capturedPiece != NO_PIECE;

        if (capture)
        {
            run[WHITE] = run[BLACK] = 0;
            seen[WHITE] = seen[BLACK] = 0;
        }

        if (isCheck)
        {
            if (!forced)
            {
                ++run[mover];
                if (id >= 0 && id < 31)
                    seen[mover] |= uint32_t(1) << id;
            }
        }
        else
        {
            // A side's non-checking move terminates that side's check run.
            run[mover] = 0;
            seen[mover] = 0;
        }

        const int pieces = bit_count(seen[mover]);
        const int limit = pieces ? std::min(6 * pieces, 18) : 0;
        if (limit && run[mover] > limit)
        {
            violator = mover;
            return true;
        }

        const Square from = m.from_sq();
        const Square to   = m.to_sq();
        const int movedId = idAt[to];
        const bool stopAfterUndo = capture;
        rollback.undo_move(m, stp->capturedPiece);
        if (!stopAfterUndo && movedId >= 0)
            idAt[from] = movedId;
        idAt[to] = -1;
        stp = stp->previous;
        if (stopAfterUndo)
            break;

        (void) moved;
    }
    return false;
}

// TianTian practical long-chase limit: a side may not make a seventh
// consecutive chase move.  We deliberately reuse Pikafish's existing chased()
// legality detector so pins, recaptures, protected attacks and the platform's
// piece-strength exceptions remain aligned with Pikafish's mature chase logic.
bool Position::sky_chase_limit_violation(Color& violator) const {
    Position rollback;
    std::memcpy((void*) &rollback, (const void*) this, offsetof(Position, filter));

    int whiteId = 0, blackId = 0;
    for (Square s = SQ_A0; s <= SQ_I9; ++s)
        if (rollback.piece_on(s) != NO_PIECE)
            rollback.idBoard[s] = color_of(rollback.piece_on(s)) == WHITE ? whiteId++ : blackId++;

    StateInfo* stp = this->st;
    int run[COLOR_NB] = {0, 0};

    for (int n = 0; stp && n < 20; ++n)
    {
        const Move m = stp->move;
        if (m.null())
            break;

        const Color mover = ~rollback.side_to_move();
        const bool capture = stp->capturedPiece != NO_PIECE;
        const Piece moved = rollback.piece_on(m.to_sq());

        if (capture)
        {
            run[WHITE] = run[BLACK] = 0;
            rollback.undo_move(m, stp->capturedPiece);
            break;
        }

        if (type_of(moved) == PAWN)
        {
            run[mover] = 0;
            rollback.undo_move(m, stp->capturedPiece);
            stp = stp->previous;
            continue;
        }

        // Match the upstream cycle detector: a move counts as a chase only
        // when the moved side has a chase AFTER the move that was not already
        // present BEFORE the move.  This avoids counting a static attack as a
        // fresh chase on every ply.
        const u16 after = rollback.chased(mover);
        rollback.undo_move(m, stp->capturedPiece);
        const u16 before = rollback.chased(mover);

        if (after & ~before)
            ++run[mover];
        else
            run[mover] = 0;

        if (run[mover] >= 7)
        {
            violator = mover;
            return true;
        }

        stp = stp->previous;
    }
    return false;
}

// Classify a 2-fold repeated position.  For checking cycles, only non-forced
// checks count as the offender.  For pure chase cycles, use the upstream
// detect_chases() classifier.  Neutral repetition is returned as false here so
// it can reach the separate five-occurrence draw rule.
bool Position::sky_classify_twofold(int d, Value& result, int ply) {
    Position rollback;
    std::memcpy((void*) &rollback, (const void*) this, offsetof(Position, filter));

    // Match SkyRule's practical distinction: a perpetual-check violator must
    // be checking on every move in the repeated cycle.  Mixed check/chase loops
    // fall through to the chase classifier; this matters for cases where one
    // side's checking move forces the opponent's chase reply.
    bool allChecking[COLOR_NB] = {true, true};
    int  moveCount[COLOR_NB]   = {0, 0};

    StateInfo* stp = this->st;
    for (int i = 0; i < d && stp; ++i)
    {
        if (stp->move.null())
            return false;

        const Color mover = ~rollback.side_to_move();
        const bool forced = stp->previous && bool(stp->previous->checkersBB);
        allChecking[mover] &= bool(stp->checkersBB) && !forced;
        ++moveCount[mover];

        rollback.undo_move(stp->move, stp->capturedPiece);
        stp = stp->previous;
    }

    if (moveCount[WHITE] && moveCount[BLACK] &&
        (allChecking[WHITE] || allChecking[BLACK]))
    {
        if (allChecking[WHITE] ^ allChecking[BLACK])
        {
            const Color offender = allChecking[WHITE] ? WHITE : BLACK;
            result = sky_rule_result(offender, sideToMove);
        }
        else
            result = VALUE_DRAW;
        return true;
    }

    // A repeated non-checking / mixed cycle is a chase cycle only when an actual chase
    // exists in the cycle.  Initialize ids exactly as detect_chases() does.
    Position chaseProbe;
    std::memcpy((void*) &chaseProbe, (const void*) this, offsetof(Position, filter));
    int whiteId = 0, blackId = 0;
    for (Square s = SQ_A0; s <= SQ_I9; ++s)
        if (chaseProbe.piece_on(s) != NO_PIECE)
            chaseProbe.idBoard[s] = color_of(chaseProbe.piece_on(s)) == WHITE ? whiteId++ : blackId++;

    stp = this->st;
    bool anyChase = false;
    for (int i = 0; i < d && stp; ++i)
    {
        const Color mover = ~chaseProbe.side_to_move();
        anyChase |= bool(chaseProbe.chased(mover));
        chaseProbe.undo_move(stp->move, stp->capturedPiece);
        stp = stp->previous;
    }

    if (!anyChase)
        return false;

    Position detector;
    std::memcpy((void*) &detector, (const void*) this, offsetof(Position, filter));
    result = sky_rule_from_chase_result(detector.detect_chases(d, ply));
    return true;
}

bool Position::sky_rule_judge(Value& result, int ply) {
    // 1) The exact move that exceeds a long-check limit is a rule loss.
    // Returning -24999 in that child is what lets alpha-beta proactively avoid
    // the move at the parent, i.e. before the platform would actually forfeit.
    Color violator;
    if (st->pliesFromNull >= 13 && st->checkersBB && sky_check_limit_violation(violator))
    {
        result = sky_rule_result(violator, sideToMove);
        return true;
    }

    // 2) Same principle for the seventh consecutive practical chase move.
    if (st->pliesFromNull >= 13 && sky_chase_limit_violation(violator))
    {
        result = sky_rule_result(violator, sideToMove);
        return true;
    }

    // 3) SkyRule is 2-fold for check/chase repetition.  Search from the current
    // node backwards to the nearest repeated key on the same side-to-move.
    int occurrence = 1;
    StateInfo* stp = st->previous ? st->previous->previous : nullptr;
    int d = 2;
    if (filter[st->key] > 0)
    for (; stp; stp = stp->previous && stp->previous->previous ? stp->previous->previous : nullptr, d += 2)
    {
        if (stp->move.null())
            break;

        if (stp->key == st->key)
        {
            ++occurrence;
            Value cycleResult = VALUE_NONE;
            if (sky_classify_twofold(d, cycleResult, ply))
            {
                // Any genuine checking/chasing 2-fold is terminal under SkyRule,
                // including a draw caused by symmetric/mutual chasing.
                result = cycleResult;
                return true;
            }
        }

        if (d > st->pliesFromNull)
            break;
    }

    // 4) Neutral, non-check/non-chase repetition: 5th occurrence is a draw.
    if (occurrence >= 5)
    {
        result = VALUE_DRAW;
        return true;
    }

    return false;
}

'''
    text = text.replace(marker, helper + marker, 1)

# Dispatch SkyRule first.  The existing rule-60 / insufficient-material tail is
# deliberately preserved verbatim.
text = add_once(
    text,
    "bool Position::rule_judge(Value& result, int ply) {\n",
    "    if (SKY_RULE_ENABLED && sky_rule_judge(result, ply))\n        return true;\n",
)

# The upstream repetition implementation is not the dedicated 2-fold SkyRule
# layer; leave it compiled only for a future false setting.
rep_needle = "    if (end >= 4 && filter[st->key] >= 1)\n"
text = text.replace(rep_needle, "    if (!SKY_RULE_ENABLED && end >= 4 && filter[st->key] >= 1)\n", 1)
write(path, text)

# ---------------------------------------------------------------------------
# search.cpp
# ---------------------------------------------------------------------------
path = SRC / "search.cpp"
text = read(path)
backup(path)
root_marker = "    if (!rootNode)\n    {\n        // Step 2. Check for aborted search or repetition\n        Value result = VALUE_NONE;\n"
root_add = (
    "    // SKY_RULE_ROOT_CHECK_APPLIED\n"
    "    if (rootNode)\n"
    "    {\n"
    "        Value rootRule = VALUE_NONE;\n"
    "        if (pos.rule_judge(rootRule, ss->ply))\n"
    "            return rootRule;\n"
    "    }\n\n"
)
text = add_once(text, root_marker, root_add, before=True)
write(path, text)

# No runtime switch is added on purpose.  This is a dedicated, deterministic
# SkyRule build, avoiding version-dependent UCI Option callback signatures.

print("SkyRule overlay applied successfully.")
