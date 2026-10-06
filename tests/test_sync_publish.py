"""The sync script exists to keep private files OUT of a public repo, so the
tests are about what it must refuse to copy."""

from pathlib import Path

import pytest

from scripts import sync_publish as sp


@pytest.fixture
def world(tmp_path, monkeypatch):
    root, dest = tmp_path / "project", tmp_path / "project" / "publish"
    (dest / ".git").mkdir(parents=True)
    (dest / ".git" / "HEAD").write_text("ref: refs/heads/main")

    def put(rel, text="x"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    # publishable
    put("main.py", "print(1)"); put("requirements.txt"); put("README.md"); put("db/schema.sql")
    put("src/a.py"); put("src/sub/b.py"); put("tests/test_a.py"); put("scripts/s.py")
    put("docs/index.html"); put("docs/calc.js"); put(".github/workflows/site.yml")
    # private or generated: must never travel
    put("input/holdings.csv", "0050,2024-10-08,1000,168.5")
    put("input/watchlist.csv"); put("_context/progress.md", "private notes")
    put("_context/on-demand/journal.md"); put("output/multi-work/plan.md")
    put("db/stock.db"); put("docs/data/quotes.json"); put("docs/data/history/2330.json")
    put("src/__pycache__/a.cpython-314.pyc"); put(".venv/lib/x.py")
    put("tests/web/_seed_tmp.html"); put("src/leak.csv"); put("src/leak.db")

    monkeypatch.setattr(sp, "ROOT", root)
    monkeypatch.setattr(sp, "DEST", dest)
    return root, dest


def tracked(dest):
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(dest).parts)


def test_dry_run_changes_nothing(world):
    root, dest = world
    assert sp.main([]) == 0
    assert tracked(dest) == []


def test_apply_copies_only_the_publishable_set(world):
    root, dest = world
    assert sp.main(["--apply"]) == 0
    got = tracked(dest)
    assert got == sorted([
        ".github/workflows/site.yml", "README.md", "db/schema.sql", "docs/calc.js",
        "docs/index.html", "main.py", "requirements.txt", "scripts/s.py",
        "src/a.py", "src/sub/b.py", "tests/test_a.py"])


@pytest.mark.parametrize("private", [
    "input/holdings.csv", "input/watchlist.csv", "_context/progress.md",
    "_context/on-demand/journal.md", "output/multi-work/plan.md", "db/stock.db",
    "docs/data/quotes.json", "docs/data/history/2330.json",
    "src/__pycache__/a.cpython-314.pyc", "tests/web/_seed_tmp.html",
    "src/leak.csv", "src/leak.db",
])
def test_private_or_generated_file_is_never_copied(world, private):
    root, dest = world
    sp.main(["--apply"])
    assert not (dest / private).exists(), private


def test_removed_source_files_are_removed_from_the_mirror(world):
    root, dest = world
    sp.main(["--apply"])
    (root / "src" / "a.py").unlink()
    (root / ".github" / "workflows" / "site.yml").unlink()
    sp.main(["--apply"])
    assert not (dest / "src" / "a.py").exists()
    assert not (dest / ".github" / "workflows" / "site.yml").exists()
    assert (dest / "src" / "sub" / "b.py").exists()


def test_changed_file_is_updated(world):
    root, dest = world
    sp.main(["--apply"])
    (root / "main.py").write_text("print(2)", encoding="utf-8")
    sp.main(["--apply"])
    assert (dest / "main.py").read_text(encoding="utf-8") == "print(2)"


def test_git_metadata_and_unmanaged_files_in_the_mirror_are_left_alone(world):
    root, dest = world
    (dest / "input").mkdir()
    (dest / "input" / ".gitkeep").write_text("")
    (dest / ".gitignore").write_text("keep me")
    sp.main(["--apply"])
    assert (dest / ".git" / "HEAD").read_text() == "ref: refs/heads/main"
    assert (dest / "input" / ".gitkeep").exists()
    assert (dest / ".gitignore").read_text() == "keep me"


def test_generated_data_already_in_the_mirror_is_not_deleted_by_a_sync(world):
    """docs/data is excluded from BOTH directions: until it is deliberately
    untracked, a sync must neither copy it nor delete the mirror's copy."""
    root, dest = world
    (dest / "docs" / "data").mkdir(parents=True)
    (dest / "docs" / "data" / "old.json").write_text("{}")
    sp.main(["--apply"])
    assert (dest / "docs" / "data" / "old.json").exists()


def test_refuses_to_run_without_a_git_repo_at_the_destination(world):
    root, dest = world
    (dest / ".git" / "HEAD").unlink()
    (dest / ".git").rmdir()
    assert sp.main(["--apply"]) == 2
