"""Turning a raw Scryfall card object into a ``cards`` row.

Most fields map straight across. The complication is multi-faced cards
(``transform``, ``modal_dfc``, ``split``, ``flip``, ``adventure``, ...), where
Scryfall moves ``mana_cost``, ``oracle_text``, ``colors`` and ``image_uris``
into a ``card_faces`` array and may omit them at the card root entirely.

**Face merge rule: both faces are kept, not just the front.** Our Card model is
single-valued, so the faces are concatenated. Storing the front face alone
would be actively wrong for this application — the back of a modal DFC land is
usually the reason to run it, and the metadata generator and knowledge document
downstream would be reasoning about half a card. ``docs/knowledge-pipeline.md``
flagged this as an open question; this is the answer.

``cmc`` and ``color_identity`` are always present at the root, including on
multi-faced cards, where ``color_identity`` is the union across faces. That is
the field Commander legality depends on.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Separates the two halves of a merged oracle text. Distinct from the " // "
# used for names and mana costs so a reader (and any later parser) can tell
# which join produced which field.
FACE_TEXT_SEPARATOR = "\n//\n"
FACE_JOIN = " // "


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    The timestamp columns are plain ``DateTime`` (SQLite has no real timezone
    support), so every datetime that reaches the database is normalized to
    naive UTC. Mixing the two raises TypeError on comparison, which is exactly
    the kind of failure that only shows up on the second import.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class CardRow:
    """The subset of a Scryfall card that Tome persists."""

    oracle_id: str
    scryfall_id: str
    name: str
    mana_cost: str | None
    mana_value: float
    oracle_text: str | None
    colors: list[str]
    color_identity: list[str]
    type_line: str
    keywords: list[str]
    image_url: str | None
    layout: str
    legalities: dict[str, str]
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "scryfall_id": self.scryfall_id,
            "name": self.name,
            "mana_cost": self.mana_cost,
            "mana_value": self.mana_value,
            "oracle_text": self.oracle_text,
            "colors": self.colors,
            "color_identity": self.color_identity,
            "type_line": self.type_line,
            "keywords": self.keywords,
            "image_url": self.image_url,
            "layout": self.layout,
            "legalities": self.legalities,
            "updated_at": self.updated_at,
        }


def _faces(card: dict) -> list[dict]:
    faces = card.get("card_faces")
    return faces if isinstance(faces, list) else []


def _merged(card: dict, key: str, separator: str) -> str | None:
    """Read ``key`` from the root, falling back to joining it across faces."""
    root = card.get(key)
    if root not in (None, ""):
        return root

    parts = [face.get(key) for face in _faces(card)]
    present = [part for part in parts if part not in (None, "")]
    if not present:
        return None
    return separator.join(present)


def _merged_oracle_text(card: dict) -> str | None:
    """Oracle text, with each face labelled by name when there's more than one.

    Labelling matters downstream: an unlabelled concatenation of two rules
    boxes reads as one card with contradictory text, which is exactly the wrong
    input for the metadata generator.
    """
    root = card.get("oracle_text")
    if root not in (None, ""):
        return root

    chunks: list[str] = []
    for face in _faces(card):
        text = face.get("oracle_text")
        if text in (None, ""):
            continue
        name = face.get("name")
        chunks.append(f"{name}\n{text}" if name else text)

    return FACE_TEXT_SEPARATOR.join(chunks) if chunks else None


def _merged_colors(card: dict) -> list[str]:
    root = card.get("colors")
    if root:
        return list(root)

    seen: list[str] = []
    for face in _faces(card):
        for color in face.get("colors") or []:
            if color not in seen:
                seen.append(color)
    return seen


def _image_url(card: dict) -> str | None:
    images = card.get("image_uris") or {}
    if images.get("normal"):
        return images["normal"]
    # Transform-style cards carry images per face; the front face is the right
    # one to show in a deck list.
    for face in _faces(card):
        face_images = face.get("image_uris") or {}
        if face_images.get("normal"):
            return face_images["normal"]
    return None


def to_card_row(card: dict, *, imported_at: datetime | None = None) -> CardRow | None:
    """Map a raw Scryfall card object to a :class:`CardRow`.

    Returns ``None`` when the object can't be mapped — a card with no
    ``oracle_id`` is not something we can key on. The caller counts these as
    skipped rather than letting one malformed record abort a 33,000-row import.
    """
    oracle_id = card.get("oracle_id")
    if not oracle_id:
        # Reversible cards put oracle_id on the faces rather than the root.
        for face in _faces(card):
            if face.get("oracle_id"):
                oracle_id = face["oracle_id"]
                break
    if not oracle_id:
        return None

    name = card.get("name") or _merged(card, "name", FACE_JOIN)
    type_line = card.get("type_line") or _merged(card, "type_line", FACE_JOIN)
    if not name or not type_line:
        return None

    return CardRow(
        oracle_id=oracle_id,
        scryfall_id=card.get("id", ""),
        name=name,
        mana_cost=_merged(card, "mana_cost", FACE_JOIN),
        # `cmc` is Scryfall's name for mana value and is always at the root.
        mana_value=float(card.get("cmc") or 0.0),
        oracle_text=_merged_oracle_text(card),
        colors=_merged_colors(card),
        color_identity=list(card.get("color_identity") or []),
        type_line=type_line,
        keywords=list(card.get("keywords") or []),
        image_url=_image_url(card),
        layout=card.get("layout", "normal"),
        legalities=dict(card.get("legalities") or {}),
        updated_at=imported_at or utcnow(),
    )
