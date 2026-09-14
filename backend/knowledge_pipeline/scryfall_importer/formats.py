"""Which cards a given import should accept.

**Tome is Commander-only.** Commander is the only *enabled* profile, so it is
the only pool the importer will write and the only thing a user is ever asked
to choose. That's deliberate: the UI, the deck validator and the generation
prompts all assume a 100-card singleton deck, and letting someone import a
Standard pool into a Commander app only produces confusing results.

Pioneer and Standard stay registered but disabled. They are the scaffolding
that keeps this module honestly multi-format — the registry, the resolver, the
CLI picker and `ImportRun.format_profile` all still work in terms of "a
profile", so enabling one later is `enabled=True` plus the UI work, not a
rewrite. Nothing else in the codebase hardcodes the string "commander".

A format is *data*, not behaviour: almost every Magic format reduces to
``legalities[key]`` being one of a set of accepted values, so adding Modern or
Pauper is a new entry in :data:`PROFILES` rather than a new class. The real
per-format variance is expressible as a field — ``accepted``, which exists for
formats like Vintage where a *restricted* card is legal (limited to one copy)
and a naive ``== "legal"`` check would wrongly discard Black Lotus.
"""

from dataclasses import dataclass, field

# Layouts in the oracle_cards bulk file that aren't playable cards. Excluded
# from every profile — a token or an emblem has no place in a deck list or a
# collection, and embedding them would pollute retrieval.
#
# Verified against the 2026-08-17 oracle_cards snapshot (38,626 objects), which
# removes 3,770 of them. Two that look like they belong here but don't:
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

_DEFAULT_ACCEPTED: frozenset[str] = frozenset({"legal"})


@dataclass(frozen=True)
class FormatProfile:
    """A named card pool, defined by a legality lookup."""

    name: str
    label: str
    # Key into Scryfall's `legalities` object.
    legality_key: str
    # Legality values that count as playable in this format.
    accepted: frozenset[str] = field(default=_DEFAULT_ACCEPTED)
    # Alternative names a user might type. Resolved to `name`.
    aliases: frozenset[str] = field(default=frozenset())
    # Whether the app actually supports this format today. Disabled profiles
    # are scaffolding: resolvable by name, but never importable.
    enabled: bool = False
    # Rough size, shown in the interactive picker so the choice is informed.
    # Indicative only — the real number comes from the import itself.
    approx_cards: int | None = None

    def accepts(self, card: dict) -> bool:
        """Whether ``card`` (a raw Scryfall card object) belongs in this pool."""
        if card.get("layout") in NON_CARD_LAYOUTS:
            return False
        legalities = card.get("legalities") or {}
        return legalities.get(self.legality_key) in self.accepted


PROFILES: dict[str, FormatProfile] = {
    profile.name: profile
    for profile in (
        FormatProfile(
            "commander",
            "Commander / EDH",
            "commander",
            aliases=frozenset({"edh"}),
            enabled=True,
            approx_cards=31_800,
        ),
        # --- Scaffolding below: registered, deliberately not enabled. ---
        FormatProfile("pioneer", "Pioneer", "pioneer", approx_cards=13_000),
        FormatProfile("standard", "Standard", "standard", approx_cards=4_900),
    )
}

DEFAULT_PROFILE = "commander"

# alias -> canonical name, e.g. {"edh": "commander"}.
_ALIASES: dict[str, str] = {
    alias: profile.name for profile in PROFILES.values() for alias in profile.aliases
}


class FormatError(ValueError):
    """Base for anything wrong with a requested format name."""


class UnknownFormatError(FormatError):
    """Raised when a caller names a format that isn't in the registry."""


class FormatNotEnabledError(FormatError):
    """Raised when a caller names a registered but not-yet-supported format."""


def enabled_profiles() -> dict[str, FormatProfile]:
    """The profiles a user may actually import, in registry order."""
    return {name: profile for name, profile in PROFILES.items() if profile.enabled}


def selectable_names() -> list[str]:
    """Every name (canonical or alias) an import will currently accept."""
    names = [name for name, profile in PROFILES.items() if profile.enabled]
    names += [alias for alias, target in _ALIASES.items() if PROFILES[target].enabled]
    return sorted(names)


def resolve(name: str, *, allow_disabled: bool = False) -> FormatProfile:
    """Look up a profile by name or alias, case-insensitively.

    Disabled profiles raise :class:`FormatNotEnabledError` unless
    ``allow_disabled`` is set, so nothing can quietly import a pool the rest of
    the app doesn't understand.
    """
    key = name.strip().lower()
    key = _ALIASES.get(key, key)

    try:
        profile = PROFILES[key]
    except KeyError:
        raise UnknownFormatError(
            f"Unknown format {name!r}. Available: {', '.join(selectable_names())}."
        ) from None

    if not profile.enabled and not allow_disabled:
        raise FormatNotEnabledError(
            f"{profile.label} isn't supported yet — Tome is Commander-only for now. "
            f"Available: {', '.join(selectable_names())}."
        )
    return profile
