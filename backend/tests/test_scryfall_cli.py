"""Command line behaviour: format selection and the guards on the destructive paths."""

import pytest

from knowledge_pipeline.scryfall_importer import __main__ as cli
from knowledge_pipeline.scryfall_importer.formats import PROFILES


class _StubReport:
    def summary(self) -> str:
        return "stub"


@pytest.fixture
def no_terminal(monkeypatch):
    """Stand in for Docker, CI or cron: nobody to prompt."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


@pytest.fixture
def terminal_at_eof(monkeypatch):
    """A shell that claims to be a terminal but whose stdin is already closed.

    Some CI runners and container shells do exactly this, and it used to crash
    the picker with an EOFError traceback.
    """
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))


@pytest.fixture
def stub_import(monkeypatch):
    """Capture what main() would have imported, without touching the network."""
    calls = []
    monkeypatch.setattr(cli, "import_cards", lambda **kwargs: calls.append(kwargs) or _StubReport())
    return calls


@pytest.fixture
def two_formats(monkeypatch):
    """Pretend a second profile has been enabled, to exercise the picker."""
    enabled = {name: PROFILES[name] for name in ("commander", "standard")}
    monkeypatch.setattr(cli, "enabled_profiles", lambda: enabled)
    monkeypatch.setattr(cli, "selectable_names", lambda: ["commander", "edh", "standard"])


def test_no_format_imports_commander_without_asking(no_terminal, stub_import):
    """One enabled format means there's nothing to choose — Docker and cron
    just work, with no --format to remember."""
    assert cli.main([]) == 0
    assert stub_import[0]["format_name"] == "commander"


def test_no_format_on_a_terminal_does_not_prompt(monkeypatch, stub_import):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))

    assert cli.main([]) == 0
    assert stub_import[0]["format_name"] == "commander"


def test_no_format_at_eof_does_not_traceback(terminal_at_eof, stub_import):
    assert cli.main([]) == 0
    assert stub_import[0]["format_name"] == "commander"


def test_edh_is_accepted_as_a_name_for_commander(no_terminal, stub_import):
    assert cli.main(["--format", "edh"]) == 0
    assert stub_import[0]["format_name"] == "commander"


def test_unknown_format_is_rejected(no_terminal, capsys):
    assert cli.main(["--format", "pokemon"]) == 2
    assert "Unknown format" in capsys.readouterr().err


def test_scaffolded_format_is_refused_with_an_explanation(no_terminal, capsys):
    """A Standard pool in a Commander app is exactly the confusion to avoid."""
    assert cli.main(["--format", "standard"]) == 2
    assert "Commander-only" in capsys.readouterr().err


def test_reset_and_dry_run_are_contradictory(no_terminal, capsys):
    assert cli.main(["--reset", "--dry-run"]) == 2
    assert "contradictory" in capsys.readouterr().err


def test_limit_must_be_positive(no_terminal, capsys):
    assert cli.main(["--limit", "0"]) == 2
    assert "--limit" in capsys.readouterr().err


def test_force_without_reset_says_it_was_ignored(no_terminal, stub_import, capsys):
    """Silence would let someone think they'd authorized something that never ran."""
    assert cli.main(["--force"]) == 0
    assert "only applies to --reset" in capsys.readouterr().err


def test_forced_reset_without_a_terminal_refuses(monkeypatch, no_terminal, capsys):
    """--force must not be enough on its own when nobody can confirm."""
    monkeypatch.setattr(
        cli.sink, "user_data_counts", lambda _s: {"collection": 5, "decks": 1, "deck_cards": 99}
    )
    monkeypatch.setattr(
        cli.sink, "reset", lambda *a, **k: pytest.fail("reset must not run")
    )

    assert cli.main(["--reset", "--force"]) == 1
    out = capsys.readouterr()
    assert "5 collection" in out.out
    assert "Aborted" in out.err


def test_reset_refused_when_user_data_exists(monkeypatch, no_terminal, capsys):
    def refuse(_session, *, force):
        raise cli.sink.ResetRefused("Refusing to reset: the database holds user data.")

    monkeypatch.setattr(cli.sink, "user_data_counts", lambda _s: {"collection": 3})
    monkeypatch.setattr(cli.sink, "reset", refuse)

    assert cli.main(["--reset"]) == 1
    assert "Refusing to reset" in capsys.readouterr().err


def test_typed_confirmation_requires_the_exact_word(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert not cli._typed_confirmation("? ", "delete")

    monkeypatch.setattr("builtins.input", lambda *_: " DELETE ")
    assert cli._typed_confirmation("? ", "delete")


def test_picker_returns_when_a_second_format_is_enabled(monkeypatch, two_formats):
    """The multi-format path is dormant, not gone: enabling a profile brings
    the prompt back with no other change."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "standard")

    assert cli._select_format() == "standard"


def test_picker_defaults_to_commander_on_empty_input(monkeypatch, two_formats):
    monkeypatch.setattr("builtins.input", lambda *_: "")

    assert cli._choose_format_interactively() == "commander"


def test_picker_accepts_a_format_name_as_well_as_a_number(monkeypatch, two_formats):
    monkeypatch.setattr("builtins.input", lambda *_: "edh")
    assert cli._choose_format_interactively() == "edh"

    responses = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda *_: next(responses))
    assert cli._choose_format_interactively() == "standard"


def test_picker_reprompts_on_nonsense(monkeypatch, two_formats, capsys):
    responses = iter(["99", "banana", "1"])
    monkeypatch.setattr("builtins.input", lambda *_: next(responses))

    assert cli._choose_format_interactively() == "commander"
    assert "between 1 and" in capsys.readouterr().out
