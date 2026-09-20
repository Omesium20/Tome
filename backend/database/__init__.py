"""Data layer, split by which database the entities live in.

Tome has two databases with different operators and independent migrations
(`docs/data-model.md`, `docs/architecture.md#data-boundary-two-databases-one-join-key`):

- ``database.knowledge`` — the shared cloud corpus. Cards and their AI
  metadata. Hosted by maintainers, written only by the Knowledge Pipeline.
- ``database.local`` — one user's collection and decks, on their machine.

Import from exactly one of them. A module that imports both is almost
certainly a bug: the planes are separate deployables, and the only sanctioned
path from the client to the corpus is HTTP via the Knowledge API.
"""
