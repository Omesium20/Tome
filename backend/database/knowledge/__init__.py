"""The knowledge plane's data layer: the shared card corpus.

Hosted centrally by maintainers, written only by the Knowledge Pipeline, and
read by clients over HTTP through the Knowledge API — never by a direct
connection. Nothing under `api/` or `deck_pipeline/` may import from here.
"""
