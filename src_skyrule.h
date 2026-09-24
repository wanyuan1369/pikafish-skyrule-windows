#pragma once

#include "types.h"

namespace Stockfish {

// Dedicated SkyRule build for Shark Xiangqi / TianTian-compatible adjudication.
// Kept below the normal mate threshold so UCI prints it as centipawns:
//   cp 24999  -> side to move is winning under the rule
//  cp -24999  -> side to move is losing under the rule
inline constexpr Value SKY_RULE_WIN  = Value(24999);
inline constexpr Value SKY_RULE_LOSS = Value(-24999);

// This overlay intentionally builds a dedicated SkyRule engine.
// No runtime switch is required, which makes the build deterministic.
inline constexpr bool SKY_RULE_ENABLED = true;

}  // namespace Stockfish
