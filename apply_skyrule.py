#!/usr/bin/env python3
"""Apply AsianRule (Run #11) + SkyRule overlay to an official Pikafish checkout.

Order of patching:
1. AsianRule patch (from Run #11): ChineseRule, MateThreatDepth, UCI options
2. SkyRule patch: long-check limit, long-chase limit, 2-fold repetition
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def die(msg: str) -> None:
    raise SystemExit(f"Patch aborted: {msg}")


def read(path: Path) -> str:
    if not path.exists():
        die(f"missing {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".pre-patch")
    if not bak.exists():
        shutil.copy2(path, bak)


def add_once(text: str, needle: str, addition: str, *, before: bool = False) -> str:
    if addition in text:
        return text
    if needle not in text:
        die(f"anchor not found: {needle[:160]!r}")
    return text.replace(needle, addition + needle if before else needle + addition, 1)


# ===========================================================================
# STEP 1: AsianRule patch (Run #11)
# ===========================================================================

# ---------------------------------------------------------------------------
# position.h - AsianRule additions
# ---------------------------------------------------------------------------
path = SRC / "position.h"
text = read(path)
backup(path)

# Add ChineseRule / MateThreatDepth extern declarations
text = add_once(
    text,
    "class TranspositionTable;\nstruct SharedHistories;\n",
    "// XQ repetition-rule options, set from the UCI layer (engine.cpp)\n"
    "extern bool ChineseRule;
extern bool SkyRule;\n"
    "extern int  MateThreatDepth;\n"
)

# Add has_mate_threat method declaration
text = add_once(
    text,
    "    Value                 detect_chases(int d, int ply = 0);\n"
    "    bool                  chase_legal(Move m) const;\n",
    "    bool                  has_mate_threat(Depth d = -1);\n"
)

write(path, text)

# ---------------------------------------------------------------------------
# position.cpp - AsianRule additions
# ---------------------------------------------------------------------------
path = SRC / "position.cpp"
text = read(path)
backup(path)

# Add global variable definitions
text = add_once(
    text,
    "using namespace Attacks;\n",
    "// XQ repetition-rule option state (defaults: Asian rule)\n"
    "bool ChineseRule    = false;
bool SkyRule        = false;\n"
    "int  MateThreatDepth = 1;\n"
)

# Replace detect_chases function body with AsianRule version
old_detect_start = "Value Position::detect_chases(int d, int ply) {"
old_detect_end = "    return bool(chase[us]) ^ bool(chase[them]) ? chase[us] ? mated_in(ply) : mate_in(ply)\n" \
                 "                                               : VALUE_DRAW;\n" \
                 "}\n"

# Find the old detect_chases function and replace it
if old_detect_start in text:
    # Find the end of the function
    start_idx = text.find(old_detect_start)
    end_marker = "                                               : VALUE_DRAW;\n}"
    end_idx = text.find(end_marker, start_idx)
    if end_idx == -1:
        die("Could not find end of detect_chases function")
    end_idx += len(end_marker)

    new_detect = r'''Value Position::detect_chases(int d, int ply) {
    Color us = sideToMove, them = ~us;

    // Rollback until we reached st - d
    u16 rooks[COLOR_NB]    = {0xFFFF, 0xFFFF};
    u16 chase[COLOR_NB]     = {0xFFFF, 0xFFFF};
    u16 newChase[COLOR_NB] = {};
    newChase[us] = chased(us);
    for (int i = 0; i < d; ++i)
    {
        if (!chase[~sideToMove])
        {
            if (!chase[sideToMove])
                break;
            std::swap(chase[sideToMove], chase[~sideToMove]);
        }
        else
        {
            if (st->checkersBB || (ChineseRule && MateThreatDepth && has_mate_threat()))
            {
                // Redirect *check* and *mate threat* to *chase all pieces* in Chinese Rule
                chase[~sideToMove] &= ChineseRule ? 0xFFFF : 0;
                rooks[~sideToMove]   = 0;
                undo_move(st->move, st->capturedPiece);
                st = st->previous;
            }
            else
            {
                u16 oldChase = chased(~sideToMove);
                // Calculate rooks pinned by knight
                u16 flag = 0;
                if (!ChineseRule && rooks[~sideToMove]
                    && (blockers_for_king(sideToMove) & pieces(sideToMove, ROOK)))
                {
                    Bitboard knights = pinners(~sideToMove) & pieces(~sideToMove, KNIGHT);
                    while (knights)
                    {
                        Square s = pop_lsb(knights);
                        Bitboard b = between_bb(king_square(sideToMove), s) ^ s;
                        s = pop_lsb(b);
                        if (piece_on(s) == make_piece(sideToMove, ROOK))
                            flag |= 1 << idBoard[s];
                    }
                }
                undo_move(st->move, st->capturedPiece);
                st = st->previous;
                // Take the exact diff to detect the chase
                u16 chases = oldChase & ~newChase[sideToMove];
                newChase[sideToMove] = chased(sideToMove);
                if (ChineseRule)
                    chases = oldChase & ~newChase[sideToMove];
                else if (i == d - 2)
                    chases &= ~newChase[sideToMove];
                rooks[sideToMove] &= chases & flag;
                // Redirect *chase* to *chase all pieces* in Chinese Rule
                chase[sideToMove] &= (ChineseRule && chases) ? 0xFFFF : chases;
            }
        }
    }

    // Overrides chases if rooks pinned by knight is being chased
    if ((!chase[us] && !chase[them]) || (rooks[us] && rooks[them]))
        return VALUE_DRAW;
    else if (rooks[us])
        return mated_in(ply);
    else if (rooks[them])
        return mate_in(ply);

    return !chase[us] ? mate_in(ply) : !chase[them] ? mated_in(ply) : VALUE_DRAW;
}


// Calculate mate threat within MateThreatDepth plies (d == -1: null-move probe)
bool Position::has_mate_threat(Depth d) {

    bool mateThreat = false;
    if (d == -1)
    {
        // Use null move to detect mate threats
        StateInfo nullSt;
        do_null_move(nullSt);
        mateThreat = has_mate_threat(0);
        undo_null_move();
    }
    else if (d < MateThreatDepth)
    {
        StateInfo tempSt[2];
        // Try all check moves and see if we can continuously check to get a mate
        for (const auto& check : MoveList<LEGAL>(*this))
        {
            if (gives_check(check))
            {
                do_move(check, tempSt[0]);
                bool solvable = false;
                for (const auto& evasion : MoveList<LEGAL>(*this))
                {
                    do_move(evasion, tempSt[1]);
                    solvable = !has_mate_threat(d + 1);
                    undo_move(evasion);
                    // If there exists any evasion, the check is solvable
                    if (solvable)
                        break;
                }
                undo_move(check);
                // If there exists any check that is not solvable, there is a mate threat
                if (!solvable)
                    return true;
            }
        }
    }
    return mateThreat;
}'''

    text = text[:start_idx] + new_detect + text[end_idx:]
else:
    die("Could not find detect_chases function start")

write(path, text)

# ---------------------------------------------------------------------------
# engine.cpp - AsianRule UCI options
# ---------------------------------------------------------------------------
path = SRC / "engine.cpp"
text = read(path)
backup(path)

text = add_once(
    text,
    '    options.add("nodestime", Option(0, 0, 10000));\n',
    '    options.add("Mate Threat Depth", Option(1, 0, 10, [](const Option& o) {\n'
    '        MateThreatDepth = int(o);\n'
    '        return std::nullopt;\n'
    '    }));\n'
    '\n'
    '    options.add("Repetition Rule", Option("AsianRule var AsianRule var ChineseRule var SkyRule", "AsianRule",\n'
    '      [](const Option& o) {\n'
    '          ChineseRule = (o == "ChineseRule");
          SkyRule = (o == "SkyRule");\n'
    '          return std::nullopt;\n'
    '      }));\n'
)

write(path, text)

# ---------------------------------------------------------------------------
# ucioption.cpp - combo parsing fix
# ---------------------------------------------------------------------------
path = SRC / "ucioption.cpp"
text = read(path)
backup(path)

# Fix the combo option parsing bug
old_combo = """        std::string        token;
        std::istringstream ss(defaultValue);
        while (ss >> token)
            comboMap.add(token, Option());"""
new_combo = """        std::string        token;
        std::istringstream ss(defaultValue);
        while (ss >> token)
        {
            if (token == "var" || comboMap.count(token))
                continue;
            comboMap.add(token, Option());
        }"""

if old_combo in text:
    text = text.replace(old_combo, new_combo, 1)
else:
    # Try a more flexible match
    text = re.sub(
        r'(std::string\s+token;\s*\n\s*std::istringstream ss\(defaultValue\);\s*\n\s*while \(ss >> token\)\s*\n)\s*comboMap\.add\(token, Option\(\)\);',
        r'''\1        {
            if (token == "var" || comboMap.count(token))
                continue;
            comboMap.add(token, Option());
        }''',
        text,
        count=1
    )

write(path, text)


# ===========================================================================
# STEP 2: SkyRule patch
# ===========================================================================

# The build is a dedicated SkyRule binary: no runtime ambiguity.
header = ROOT / "src_skyrule.h"
write(SRC / "skyrule.h", header.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# position.h - SkyRule additions
# ---------------------------------------------------------------------------
path = SRC / "position.h"
text = read(path)

text = add_once(
    text,
    "    bool  rule_judge(Value& result, int ply = 0);\n",
    "    bool  sky_rule_judge(Value& result, int ply = 0);\n",
)

# Private helpers
private_anchor = "    Value                 detect_chases(int d, int ply = 0);\n"
private_add = (
    "    bool sky_check_limit_violation(Color& violator) const;\n"
    "    bool sky_chase_limit_violation(Color& violator) const;\n"
    "    bool sky_classify_twofold(int d, Value& result, int ply);\n"
)
text = add_once(text, private_anchor, private_add)
write(path, text)

# ---------------------------------------------------------------------------
# position.cpp - SkyRule additions
# ---------------------------------------------------------------------------
path = SRC / "position.cpp"
text = read(path)

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

// TianTian practical long-chase limit: 7 consecutive chase moves
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

// Classify a 2-fold repeated position (TianTian precise logic)
bool Position::sky_classify_twofold(int d, Value& result, int ply) {
    Position rollback;
    std::memcpy((void*) &rollback, (const void*) this, offsetof(Position, filter));

    // Track per-side:
    // - checkCount: how many non-forced checks
    // - chaseCount: how many moves that create new chase
    // - chaseIntersection: intersection of all chased targets across moves
    // - allCheck: is every move a non-forced check?
    int  checkCount[COLOR_NB] = {0, 0};
    int  chaseCount[COLOR_NB] = {0, 0};
    int  moveCount[COLOR_NB]  = {0, 0};
    bool allCheck[COLOR_NB]   = {true, true};
    u16  chaseIntersection[COLOR_NB] = {0xFFFF, 0xFFFF};  // intersection of chased targets

    StateInfo* stp = this->st;
    for (int i = 0; i < d && stp; ++i)
    {
        if (stp->move.null())
            return false;

        const Color mover = ~rollback.side_to_move();
        const bool forced = stp->previous && bool(stp->previous->checkersBB);
        const bool isCheck = bool(stp->checkersBB);
        const bool capture = stp->capturedPiece != NO_PIECE;

        ++moveCount[mover];

        // Count non-forced checks
        if (isCheck && !forced)
            ++checkCount[mover];
        allCheck[mover] &= (isCheck && !forced);

        // Count chase: did this move create new chase?
        // (after undoing, we compare chased() before vs after the move)
        const u16 after = rollback.chased(mover);
        rollback.undo_move(stp->move, stp->capturedPiece);
        const u16 before = rollback.chased(mover);

        // Newly created chase targets
        const u16 newChases = after & ~before;

        if (newChases)
            ++chaseCount[mover];

        // Intersection of all chase targets across moves
        chaseIntersection[mover] &= newChases;

        stp = stp->previous;

        // Captures break the cycle
        if (capture)
            return false;
    }

    // Need moves from both sides
    if (moveCount[WHITE] == 0 || moveCount[BLACK] == 0)
        return false;

    const int halfCycle = d / 2;  // moves per side in the cycle

    // Rule 1: Long check (all moves are non-forced checks)
    if (allCheck[WHITE] || allCheck[BLACK])
    {
        if (allCheck[WHITE] ^ allCheck[BLACK])
        {
            const Color offender = allCheck[WHITE] ? WHITE : BLACK;
            result = sky_rule_result(offender, sideToMove);
            return true;
        }
        else
        {
            // Both sides long check: red must change (TianTian rule)
            result = sky_rule_result(WHITE, sideToMove);
            return true;
        }
    }

    // Rule 2: Long chase (same target chased every move)
    // Long chase definition: there exists an enemy piece that is chased in EVERY move
    // = chaseIntersection != 0
    const bool whiteLongChase = (chaseIntersection[WHITE] != 0);
    const bool blackLongChase = (chaseIntersection[BLACK] != 0);

    if (whiteLongChase || blackLongChase)
    {
        if (whiteLongChase ^ blackLongChase)
        {
            const Color offender = whiteLongChase ? WHITE : BLACK;
            result = sky_rule_result(offender, sideToMove);
            return true;
        }
        else
        {
            // Both sides long chase: red must change (TianTian rule)
            result = sky_rule_result(WHITE, sideToMove);
            return true;
        }
    }

    // Rule 3: Check-chase cycle (alternating check and chase, same piece)
    // TianTian: one-piece check-chase cycle is a violation
    // e.g. one move checks, next move chases, repeating
    // Detect: half the moves are check, half are chase, and there's consistency
    const bool whiteCheckChase = (checkCount[WHITE] > 0) && (chaseCount[WHITE] > 0);
    const bool blackCheckChase = (checkCount[BLACK] > 0) && (chaseCount[BLACK] > 0);

    if (whiteCheckChase ^ blackCheckChase)
    {
        const Color offender = whiteCheckChase ? WHITE : BLACK;
        result = sky_rule_result(offender, sideToMove);
        return true;
    }

    // Rule 4: Both sides have check-chase cycle
    if (whiteCheckChase && blackCheckChase)
    {
        // TianTian: red must change first when both violate
        result = sky_rule_result(WHITE, sideToMove);
        return true;
    }

    // Note: pure chase cycle with different targets (one piece chases different pieces)
    // is allowed in TianTian, not a violation (e.g. case 3)
    // Only same-target long chase counts, which we already handled in Rule 2

    // Neutral cycle: draw
    result = VALUE_DRAW;
    return true;
}

bool Position::sky_rule_judge(Value& result, int ply) {
    // 1) Long-check limit violation
    Color violator;
    if (st->pliesFromNull >= 13 && st->checkersBB && sky_check_limit_violation(violator))
    {
        result = sky_rule_result(violator, sideToMove);
        return true;
    }

    // 2) Long-chase limit violation
    if (st->pliesFromNull >= 13 && sky_chase_limit_violation(violator))
    {
        result = sky_rule_result(violator, sideToMove);
        return true;
    }

    // 3) 2-fold repetition check/chase
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
                result = cycleResult;
                return true;
            }
        }

        if (d > st->pliesFromNull)
            break;
    }

    // 4) Neutral repetition: 5th occurrence is draw
    if (occurrence >= 5)
    {
        result = VALUE_DRAW;
        return true;
    }

    return false;
}

'''
    text = text.replace(marker, helper + marker, 1)

# Dispatch SkyRule first
text = add_once(
    text,
    "bool Position::rule_judge(Value& result, int ply) {\n",
    "    if (SkyRule && sky_rule_judge(result, ply))\n        return true;\n",
)

write(path, text)

# ---------------------------------------------------------------------------
# search.cpp - SkyRule root check
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

print("AsianRule + SkyRule overlay applied successfully.")
