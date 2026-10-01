"""CLI for the anchor-recalibration loop.

    python -m knowledge_pipeline.anchor_bench example  -o pool.json
    python -m knowledge_pipeline.anchor_bench validate    pool.json
    python -m knowledge_pipeline.anchor_bench rank        pool.json -o ranked.json
    python -m knowledge_pipeline.anchor_bench page      ranked.json -o bench.html
    # ... review, copy the selection out of the page's last tab ...
    python -m knowledge_pipeline.anchor_bench export    ranked.json --selection sel.json

Read-only with respect to both databases: `validate` and `--fill-facts` query
the knowledge corpus and nothing here ever writes to it. The only files written
are the ones named with `-o`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import example as example_module
from .export import SelectionError, load_selection, render_registry, selection_problems
from .page import write_page
from .pool import (
    CandidatePool,
    fill_card_facts,
    load_pool,
    missing_ladders,
    summarize,
    validate_against_corpus,
    validate_pool,
    write_pool,
)
from .ranking import rank_pool


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_pipeline.anchor_bench",
        description=(
            "Rebuild the anchor ladders in metadata_generator/anchors.py. Proposes nothing and "
            "decides nothing: it validates a candidate pool, ranks within each rung as triage, "
            "and builds the page a human picks the rungs on."
        ),
        epilog=(
            "The ranking is not a verdict. On the pool behind the shipped ladders the top two "
            "candidates tied exactly in 101 of 260 rungs, which is why the page applies nothing "
            "automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("example", help="write a small, real, valid pool to work from")
    ex.add_argument("-o", "--out", type=Path, default=Path("anchor-pool.json"))

    val = sub.add_parser(
        "validate",
        help="structural checks, plus every card against the knowledge corpus",
        description=(
            "Two passes. The offline pass checks bands, ratings inside their band, and the two "
            "whole-set rules (no card at two ratings, no card in two ladders). The corpus pass "
            "checks each candidate really is the card it claims -- a rung is quoted verbatim into "
            "every generation prompt, so a misremembered card is a permanent invisible error."
        ),
    )
    val.add_argument("pool", type=Path)
    val.add_argument("--offline", action="store_true",
                     help="skip the corpus pass (no database needed)")
    val.add_argument("--fill-facts", action="store_true",
                     help="overwrite each candidate's printed facts with the corpus's own, then "
                          "write the pool back in place")

    rank = sub.add_parser("rank", help="score and rank candidates within each rung")
    rank.add_argument("pool", type=Path)
    rank.add_argument("-o", "--out", type=Path, required=True)
    rank.add_argument("--close-calls", type=Path,
                      help="also write the rungs whose top two are within 0.75 here")

    page = sub.add_parser("page", help="build the self-contained human review page")
    page.add_argument("pool", type=Path)
    page.add_argument("-o", "--out", type=Path, required=True)
    page.add_argument("--fragment", action="store_true",
                      help="omit the <!doctype>/<html>/<head>/<body> skeleton, for publishing as "
                           "an artifact (that publisher supplies its own)")

    exp = sub.add_parser(
        "export",
        help="render the reviewed ladders as source for anchors.py",
        description=(
            "Takes the selection the review page hands back and writes the "
            "register(AnchorLadder(...)) calls to paste into "
            "metadata_generator/anchors.py. Only reviewed ladders are rendered."
        ),
    )
    exp.add_argument("pool", type=Path, help="the ranked pool the selection was made against")
    exp.add_argument("--selection", type=Path, required=True)
    exp.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    exp.add_argument("--no-header", action="store_true",
                     help="omit the section banner, when appending to a file that has one")
    return parser


def _report(problems: list, label: str) -> None:
    if not problems:
        print(f"{label}: clean")
        return
    print(f"{label}: {len(problems)} problem(s)")
    for problem in problems:
        print(f"  - {problem}")


def _cmd_example(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(example_module.example_pool(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"wrote {out}")
    print("One ladder, five rungs. Validate it, then replace it with a real pool.")
    return 0


def _cmd_validate(path: Path, *, offline: bool, fill_facts: bool) -> int:
    pool = load_pool(path)
    print(", ".join(f"{k.replace('_', ' ')} {v}" for k, v in summarize(pool).items()))

    changed = 0
    if fill_facts:
        changed = fill_card_facts(pool)
        write_pool(pool, path)
        print(f"filled printed facts from the corpus for {changed} candidate(s); rewrote {path}")

    structural = validate_pool(pool)
    _report(structural, "structure")

    corpus: list = []
    if offline:
        print("corpus: skipped (--offline)")
    else:
        corpus = validate_against_corpus(pool)
        _report(corpus, "corpus")

    unanchored = missing_ladders(pool)
    if unanchored:
        print(f"no ladder proposed for {len(unanchored)} tag(s): {', '.join(unanchored)}")
        print("  A tag is either fully anchored or openly unanchored -- fine, as long as it is "
              "a decision rather than an oversight.")
    return 1 if structural or corpus else 0


def _cmd_rank(path: Path, out: Path, close_calls: Path | None) -> int:
    pool = load_pool(path)
    stats = rank_pool(pool)
    write_pool(pool, out)

    print(f"scored {stats['scored']} candidates across {stats['rungs']} rungs -> {out}")
    print(f"no oracle-text evidence for their tag: {stats['no_text_evidence']}")
    if stats["conflicted_cards"]:
        print(f"rated two ways across ladders: {', '.join(stats['conflicted_cards'])}")
    ties = stats["exact_ties"]
    close = len(stats["close_calls"])
    print(f"top two exactly tied in {ties} rung(s); within 0.75 in {close}")
    print("  That is the ranking telling you how little it decided. Those rungs are yours.")
    if close_calls:
        close_calls.write_text("\n".join(stats["close_calls"]), encoding="utf-8")
        print(f"close calls written to {close_calls}")
    return 0


def _cmd_page(path: Path, out: Path, *, fragment: bool) -> int:
    pool: CandidatePool = load_pool(path)
    unranked = [c for _l, _r, c in pool.all_candidates() if c.score is None]
    if unranked:
        print(f"note: {len(unranked)} candidate(s) carry no score. The page still works -- every "
              "rung opens on its first candidate -- but run `rank` first to get the ordering, the "
              "stars and the per-candidate reasoning.")
    written = write_page(pool, out, fragment=fragment)
    print(f"wrote {written} ({written.stat().st_size // 1024} KB)")
    print("Open it, work through the Ladders tab, mark each ladder reviewed, then copy the "
          "generated registry from the Export tab into metadata_generator/anchors.py.")
    return 0


def _cmd_export(path: Path, selection_path: Path, out: Path | None, *, header: bool) -> int:
    pool = load_pool(path)
    selection = load_selection(selection_path)

    problems = selection_problems(pool, selection)
    if problems:
        print(f"{len(problems)} problem(s) with the chosen cards:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("Nothing written. Fix the selection in the page and export again.", file=sys.stderr)
        return 1

    try:
        source = render_registry(pool, selection, header=header)
    except SelectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source.rstrip("\n") + "\n", encoding="utf-8")
        print(f"wrote {out} ({len(selection.get('reviewed', []))} ladder(s))")
    else:
        print(source)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "example":
        return _cmd_example(args.out)
    if args.command == "validate":
        return _cmd_validate(args.pool, offline=args.offline, fill_facts=args.fill_facts)
    if args.command == "rank":
        return _cmd_rank(args.pool, args.out, args.close_calls)
    if args.command == "page":
        return _cmd_page(args.pool, args.out, fragment=args.fragment)
    if args.command == "export":
        return _cmd_export(args.pool, args.selection, args.out, header=not args.no_header)
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse makes this unreachable


if __name__ == "__main__":
    sys.exit(main())
