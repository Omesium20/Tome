"""Command line behaviour: argument handling and the guard on the destructive path.

Format selection used to live here — a --format flag, an interactive picker,
and the refusals around them. The import takes the whole card pool now, so
all of that is gone rather than defaulted.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.knowledge.models import Base as KnowledgeBase, Card
from knowledge_pipeline.scryfall_importer import __main__ as cli


class _StubReport:
    def summary(self) -> str:
        return "stub"


@pytest.fixture
def no_terminal(monkeypatch):
    """Stand in for Docker, CI or cron: nobody to prompt."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


@pytest.fixture
def stub_import(monkeypatch):
    """Capture what main() would have imported, without touching the network."""
    calls = []
    monkeypatch.setattr(cli, "import_cards", lambda **kwargs: calls.append(kwargs) or _StubReport())
    return calls


def _card(oracle_id: str) -> Card:
    return Card(
        oracle_id=oracle_id,
        scryfall_id=f"print-{oracle_id}",
        name=f"Card {oracle_id}",
        mana_cost="{G}",
        mana_value=1.0,
        oracle_text=None,
        colors=["G"],
        color_identity=["G"],
        type_line="Creature — Test",
        power="1",
        toughness="1",
        loyalty=None,
        defense=None,
        keywords=[],
        image_url=None,
        layout="normal",
        legalities={"commander": "legal"},
        updated_at=datetime(2026, 1, 1),
    )


@pytest.fixture
def knowledge_db(monkeypatch, tmp_path):
    """A real knowledge database for the CLI's reset path to open.

    File-backed rather than in-memory because the confirmation prompt asks for
    the database *name*, and an in-memory URL hasn't got one.
    """
    from database.knowledge import session as knowledge_session

    path = tmp_path / "tome_knowledge.db"
    engine = create_engine(f"sqlite:///{path}")
    KnowledgeBase.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)

    with maker() as session:
        session.add_all([_card("a"), _card("b")])
        session.commit()

    # _run_reset imports these inside the function body, so patching the
    # module attributes is enough — no engine is built from real settings.
    monkeypatch.setattr(knowledge_session, "get_engine", lambda: engine)
    monkeypatch.setattr(knowledge_session, "new_session", maker)

    reset_calls = []
    real_reset = cli.sink.reset

    def counting_reset(session):
        reset_calls.append(session)
        return real_reset(session)

    monkeypatch.setattr(cli.sink, "reset", counting_reset)

    try:
        yield SimpleNamespace(name=str(path), reset_calls=reset_calls)
    finally:
        engine.dispose()


def test_no_arguments_imports_everything(no_terminal, stub_import):
    """Docker and cron just work: nothing to select, nothing to prompt for."""
    assert cli.main([]) == 0
    assert stub_import == [
        {"dry_run": False, "limit": None, "if_newer": False, "force_download": False}
    ]


def test_format_is_no_longer_accepted(no_terminal, capsys):
    """Removed rather than ignored — a flag that silently does nothing is
    worse than one that errors."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--format", "commander"])

    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_prune_is_no_longer_accepted(no_terminal, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--prune"])

    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_reset_and_dry_run_are_contradictory(no_terminal, capsys):
    assert cli.main(["--reset", "--dry-run"]) == 2
    assert "contradictory" in capsys.readouterr().err


def test_limit_must_be_positive(no_terminal, capsys):
    assert cli.main(["--limit", "0"]) == 2
    assert "--limit" in capsys.readouterr().err


def test_reset_without_a_terminal_refuses(no_terminal, knowledge_db, capsys):
    """Unattended callers must never fall through a destructive confirmation.

    There is no --force to override this any more. The flag existed to get
    past the user-data check, and that check is gone — so the only remaining
    guard has to be the one that can't be waived.
    """
    assert cli.main(["--reset"]) == 1
    out = capsys.readouterr()
    assert "Aborted" in out.err
    assert not knowledge_db.reset_calls


def test_reset_names_the_database_it_is_about_to_empty(monkeypatch, knowledge_db, capsys):
    """Wrong-database is the hazard that replaced wrong-data, so say which one."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "not-the-database-name")

    assert cli.main(["--reset"]) == 1
    out = capsys.readouterr()
    assert "2 cards" in out.out          # the count it would have deleted
    assert knowledge_db.name in out.out  # the database it would have hit
    assert "Aborted" in out.err
    assert not knowledge_db.reset_calls


def test_reset_proceeds_when_the_database_name_is_typed(monkeypatch, knowledge_db, stub_import, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: knowledge_db.name)

    assert cli.main(["--reset"]) == 0
    assert len(knowledge_db.reset_calls) == 1
    assert "removed 2 cards" in capsys.readouterr().out


def test_typed_confirmation_requires_an_exact_match(monkeypatch):
    """Exact, not case-folded: the expected value is a database identifier now,
    and those aren't always lowercase."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert not cli._typed_confirmation("? ", "tome_knowledge")

    monkeypatch.setattr("builtins.input", lambda *_: "TOME_KNOWLEDGE")
    assert not cli._typed_confirmation("? ", "tome_knowledge")

    # Surrounding whitespace is still forgiven — a paste often carries it.
    monkeypatch.setattr("builtins.input", lambda *_: " tome_knowledge ")
    assert cli._typed_confirmation("? ", "tome_knowledge")
