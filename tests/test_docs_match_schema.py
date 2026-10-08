"""Keeps the documentation honest: the ER diagram must match the real schema."""
import re
from pathlib import Path

from carbon_cost import db

DOCS = Path(__file__).resolve().parents[1] / "docs"
REL = re.compile(r"^\s*(\w+) [|o}{]+--[|o}{]+ (\w+) :", re.M)


def _diagram():
    return (DOCS / "er_diagram.mmd").read_text(encoding="utf-8")


def test_every_table_and_column_in_the_diagram_is_real(conn):
    real = {
        t: [(r["name"], r["type"].lower()) for r in conn.execute(f"PRAGMA table_info({t})")]
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    drawn = {}
    for name, body in re.findall(r"^    (\w+) \{\n(.*?)^    \}", _diagram(), flags=re.S | re.M):
        cols = [line.split() for line in body.strip().splitlines()]
        drawn[name] = [(c[1], {"int": "integer"}.get(c[0], c[0])) for c in cols]
    assert drawn == real


def test_every_foreign_key_is_drawn_and_nothing_else(conn):
    fks = set()
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for r in conn.execute(f"PRAGMA foreign_key_list({t})"):
            fks.add(frozenset([t, r["table"]]))
    drawn = {frozenset(pair) for pair in REL.findall(_diagram())}
    assert drawn == fks


def test_design_note_diagram_has_the_same_links_as_the_full_diagram():
    note = (DOCS / "design_note.md").read_text(encoding="utf-8")
    block = re.search(r"```mermaid\n(.*?)```", note, flags=re.S).group(1)
    assert sorted(REL.findall(block)) == sorted(REL.findall(_diagram()))


def test_tests_named_in_the_design_note_exist():
    note = (DOCS / "design_note.md").read_text(encoding="utf-8")
    all_tests = "\n".join(p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("test_*.py"))
    for name in set(re.findall(r"`(test_\w+)`", note)):
        assert f"def {name}(" in all_tests, name


def test_functions_named_in_the_design_note_exist():
    note = (DOCS / "design_note.md").read_text(encoding="utf-8")
    for name in set(re.findall(r"`(supersede_\w+)`", note)):
        assert callable(getattr(db, name)), name
