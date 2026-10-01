"""Build the review page a human actually picks the rungs on.

One self-contained HTML file: no server, no build step, no network. It opens by
double-click and it can equally be published as an artifact for review
elsewhere, which is why the data is injected inline rather than fetched — a
sibling `.json` would need a web server locally and would be blocked by the
artifact CSP remotely.

The page itself is `bench.html`, kept as a template beside this module rather
than embedded in Python, so the markup, CSS and review logic can be edited as
markup, CSS and review logic. Everything specific to a single calibration pass
comes from the pool: the ladders, the rubric, and the pool's own `notes`.

Three things the page does that matter to the result, and that a replacement
should keep:

- **It shows every candidate for a rung side by side.** Reading four candidates
  one at a time rewards whichever you read last.
- **It applies nothing.** A rung opens on the candidate the ranking put first,
  but the recommendation is a star and a per-ladder "apply" button, never a
  default that has already been taken.
- **It marks provenance with a neutral letter, not a colour.** Which pass found
  a card is information, not quality; a green badge for "new" would reintroduce
  visually the exact bias the ranking removes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .pool import CandidatePool

TEMPLATE = Path(__file__).with_name("bench.html")
DATA_PLACEHOLDER = "__ANCHOR_DATA__"

SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{head}
</head>
<body>
{body}
</body>
</html>
"""


def render_page(pool: CandidatePool, *, fragment: bool = False) -> str:
    """The bench as one HTML document with `pool` baked in.

    `fragment` omits the `<!doctype>`/`<html>`/`<head>`/`<body>` skeleton, for
    publishing as an artifact — that publisher supplies its own and would
    otherwise nest one document inside another. A standalone file is the default
    because opening it locally is the normal case, and a page with no doctype
    renders in quirks mode.
    """
    template = TEMPLATE.read_text(encoding="utf-8")
    if DATA_PLACEHOLDER not in template:
        raise ValueError(
            f"{TEMPLATE.name} no longer contains {DATA_PLACEHOLDER}; the page has nowhere to "
            "receive the candidate pool."
        )

    # `</script>` inside a JSON string would close the block it sits in -- the one
    # escape a JSON-in-HTML injection actually needs.
    data = json.dumps(pool.to_json(), ensure_ascii=False).replace("</", "<\\/")
    html = template.replace(DATA_PLACEHOLDER, data)
    if fragment:
        return html

    head, _, body = html.partition("</style>")
    if not body:
        return SKELETON.format(head="", body=html)
    return SKELETON.format(head=head + "</style>", body=body)


def write_page(pool: CandidatePool, path: Path | str, *, fragment: bool = False) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(pool, fragment=fragment), encoding="utf-8")
    return out
