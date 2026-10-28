"""`Theme` members decidable from a card's own Scryfall fields, without a model call.

`metadata_generator/schema.py`'s module docstring states the rule this file
implements: anything decidable from `type_line`, `keywords`, mana cost, or an
oracle-text regex belongs in the ingest step, not in a question put to the
model (`CLAUDE.md`'s "structured data first, AI reasoning second"). Five
`Theme` members fit that description exactly, and the module docstring has
named them as such since the vocabulary revision — Treasure, Food, and Clues
all key off the same "create a `___` token" shape, Energy off the `{E}`
symbol, and Infect off a `keywords` lookup — but nothing read that text until
now.

**Why this matters beyond saving a few tokens.** Pass one's prompt lists
every `Theme` it wants the model to *consider*; `pipeline.py` drops these five
from that listing (`prompt.build_pass_one_system_prompt`) precisely because a
tag the model never has to weigh is a tag it cannot mis-apply. Fewer live
judgment calls in the menu means more of the model's attention on the ones
that actually require judgment — a card that supports Aristocrats or reads as
Voltron is a read of the whole card, not a substring match.

`pipeline.py` calls `derive_themes` right after pass one returns and merges
the result into `MetadataPassOne.themes` before anything downstream reads it
— ladder selection (`MetadataPassOne.ladder_tags`) and the stored row both
see the merged list, so a card that makes Treasures is still rated against
the Treasure ladder even though the model was never asked about Treasure at
all. The model keeps the vocabulary member, the type, and the ladder; only
the asking is removed.

**Plain strings in, plain strings out — no `Theme` import.** Importing
`metadata_generator.schema` runs that package's `__init__`, which imports
`pipeline` and drags in pydantic, the Anthropic SDK, and a database session
factory before this module could do anything (`cost_profile.py`'s docstring
hits the identical constraint and solves it the same way). The caller is
responsible for turning a returned string back into a `Theme` member — it
already has to import `schema` to do anything with the result.

Every regex and keyword below was checked against real oracle text for a
positive and a negative card, not written from memory of Magic templating.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-light on purpose, see module docstring
    from database.knowledge.models import Card

# The `Theme.value` strings this module can produce. Matched against
# `metadata_generator.schema.Theme` by value at the call site, not by import.
TREASURE = "Treasure"
FOOD = "Food"
CLUES = "Clues"
ENERGY = "Energy"
INFECT = "Infect"

# "Create a Treasure token", "creates two Food tokens", "create three Clue
# tokens" -- one pattern per token type rather than one shared pattern with a
# captured word, so a sentence that creates more than one type ("create a
# Treasure token and a Clue token") matches both independently instead of
# only the first. `(?:(?!\.).){0,60}?` is "up to 60 characters, none of them a
# period" -- bounds the search to roughly one clause so "You create a
# Treasure token. Sacrifice a Food: draw a card." cannot cross-match Food
# against the earlier "create". Case-insensitive because a miscapitalized
# token name should still count, not silently fail to tag.
_TOKEN_THEME_PATTERNS: dict[str, str] = {
    TREASURE: r"create[s]?\b(?:(?!\.).){0,60}?\btreasures?\s+tokens?\b",
    FOOD: r"create[s]?\b(?:(?!\.).){0,60}?\bfoods?\s+tokens?\b",
    CLUES: r"create[s]?\b(?:(?!\.).){0,60}?\bclues?\s+tokens?\b",
}
_TOKEN_THEME_RE = {
    theme: re.compile(pattern, re.IGNORECASE)
    for theme, pattern in _TOKEN_THEME_PATTERNS.items()
}

# Energy counters are rendered as the literal "{E}" symbol in oracle text
# ("You get two {E}.", "Pay {E}{E}{E}: ..."), the same way mana symbols are --
# not spelled out as a word. A substring check on the symbol is simpler and
# more reliable than matching the word "energy", which also appears in
# unrelated flavor-adjacent text. "energy counter" is kept as a fallback for
# the few reminder-text cards that spell the symbol out.
_ENERGY_RE = re.compile(r"\{E\}|energy counter", re.IGNORECASE)

# Scryfall's `keywords` list capitalizes ability names exactly as they're
# templated ("Infect", "Toxic"); `Toxic N`'s number lives only in the oracle
# text, not in this list. Matched by exact (case-insensitive) equality rather
# than substring, so a card that merely mentions "infect" or "toxic" inside
# another ability's reminder text cannot false-positive.
_INFECT_KEYWORDS = {"infect", "toxic"}


def derive_themes(card: "Card") -> list[str]:
    """`Theme.value` strings this card earns from its own fields alone.

    Order is deterministic (token themes in the order above, then Energy,
    then Infect) but carries no ranking signal the way
    `MetadataPassOne.ladder_tags`'s ordering does -- the caller decides where
    in the merged list these land.
    """
    oracle_text = card.oracle_text or ""
    themes: list[str] = [
        theme for theme, pattern in _TOKEN_THEME_RE.items() if pattern.search(oracle_text)
    ]

    if _ENERGY_RE.search(oracle_text):
        themes.append(ENERGY)

    if any(keyword.lower() in _INFECT_KEYWORDS for keyword in card.keywords):
        themes.append(INFECT)

    return themes
