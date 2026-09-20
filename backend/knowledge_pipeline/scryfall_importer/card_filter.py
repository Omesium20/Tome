"""Which objects in Scryfall's bulk file are cards we import.

**The import is not format-scoped, and there is no flag to make it one.** One
rule decides: is this object a card? If so it is imported, with its complete
`legalities` map, whatever that map happens to say.

That is a deliberate reversal. Format scoping existed so a user running the
corpus on their own machine could hold only the cards they needed. The corpus
is hosted centrally now, so nobody pays for someone else's breadth, and the
saving was never large anyway — Commander-legal cards are 91% of the card
objects in the file. Format became the wrong axis to cut on at import time:

- **Filtering costs more than it saves.** The expensive stages are metadata
  generation (one model call per card) and embedding. That is where a format
  filter belongs, and it can be applied there without re-importing anything.
- **A collection is not a legal deck.** Users own Un-set cards, memorabilia,
  and cards banned everywhere. CSV import resolves owned card *names* through
  the Knowledge API, so a card missing from the corpus resolves to nothing and
  renders as a placeholder forever. 1,945 paper-printed cards are legal in no
  format at all; excluding them would break exactly that path.
- **Format belongs to the client.** Deck building filters on
  `Card.legalities`, which every row carries in full, so a new format mode is
  a client-side predicate rather than a re-import every user waits on.

One nuance the client-side filter will need, recorded here because this module
used to own it: a **restricted** card is legal in Vintage, limited to one copy.
A naive ``legalities[fmt] == "legal"`` check wrongly discards Black Lotus.

Counts from the 2026-09-20 `oracle_cards` snapshot: 38,906 objects in, 4,075
non-card layouts excluded, **34,831 cards imported**.
"""

# Layouts in the oracle_cards bulk file that aren't playable cards. A token or
# an emblem has no place in a deck list or a collection, and embedding them
# would pollute retrieval.
#
# Verified against the 2026-09-20 oracle_cards snapshot (38,906 objects), which
# removes 4,075 of them. Two that look like they belong here but don't:
# `prepare` cards are real split-style spells, and 352 `normal` cards are
# vanilla creatures with genuinely empty oracle text.
NON_CARD_LAYOUTS: frozenset[str] = frozenset(
    {
        "token",
        "double_faced_token",
        "emblem",
        "art_series",
        "vanguard",
        "scheme",
        "planar",
        "augment",
        "host",
        # Jumpstart-style product dividers: type_line is literally "Card",
        # set_type is "memorabilia", and they have no rules text.
        "front_card",
    }
)


def is_card(raw: dict) -> bool:
    """Whether ``raw`` (a Scryfall card object) should be imported.

    Legality is deliberately not consulted. See the module docstring.
    """
    return raw.get("layout") not in NON_CARD_LAYOUTS
