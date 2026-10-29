"""The file-only boundary confines artifacts without executing submissions."""

import pytest

from agent_bench import runtime


@pytest.mark.parametrize(
    "name",
    [
        "../outside.sh",
        "/outside.sh",
        "C:/outside.sh",
        "nested\\outside.sh",
        "NUL",
        "nested/CoM1.txt",
        "trailing.",
        "trailing ",
        "name?.sh",
        "bad\x01name.sh",
    ],
)
def test_workspace_rejects_nonportable_or_escaping_write_paths(tmp_path, name):
    workspace = runtime.Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError):
        workspace.write_file(name, "command")
    assert not list(workspace.path.rglob("*"))
    assert not (tmp_path / "outside.sh").exists()


def test_read_cannot_access_a_controller_file_outside_workspace(tmp_path):
    (tmp_path / "secret.txt").write_text("controller credential")
    workspace = runtime.Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError):
        workspace.read_file("../secret.txt")


def test_regular_text_is_preserved_in_an_independent_submission_snapshot(tmp_path):
    workspace = runtime.Workspace(tmp_path / "workspace")
    workspace.write_file("notes/context.txt", "supplied context: \u03b1\n")
    workspace.write_file("solution.sh", "#!/bin/sh\nprintf 'requested command'\n")
    assert workspace.read_file("notes/context.txt") == "supplied context: \u03b1\n"
    destination = tmp_path / "submission"
    workspace.collect(destination)
    workspace.write_file("solution.sh", "revised later")
    assert (destination / "solution.sh").read_bytes() == b"#!/bin/sh\nprintf 'requested command'\n"
    assert (destination / "notes/context.txt").read_text(
        encoding="utf-8"
    ) == "supplied context: \u03b1\n"
    with pytest.raises(FileExistsError):
        workspace.collect(destination)


def test_case_alias_cannot_replace_a_portable_artifact(tmp_path):
    workspace = runtime.Workspace(tmp_path / "workspace")
    workspace.write_file("Solution.sh", "first")
    with pytest.raises(ValueError, match="collision"):
        workspace.write_file("solution.sh", "replacement")
    assert workspace.read_file("Solution.sh") == "first"


def test_workspace_rejects_link_reads_writes_and_collection(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("controller file")
    workspace = runtime.Workspace(tmp_path / "workspace")
    try:
        (workspace.path / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("The host cannot create file symlinks")
    for operation in (
        lambda: workspace.read_file("link.txt"),
        lambda: workspace.write_file("link.txt", "replacement"),
        lambda: workspace.collect(tmp_path / "submission"),
    ):
        with pytest.raises(ValueError, match="links"):
            operation()
    assert outside.read_text() == "controller file"
    assert not (tmp_path / "submission").exists()


def test_file_size_limit_preserves_existing_artifact_on_rejected_write(tmp_path):
    workspace = runtime.Workspace(tmp_path / "workspace")
    workspace.write_file("solution.sh", "keep")
    with pytest.raises(ValueError, match="write limit"):
        workspace.write_file("solution.sh", "x" * (runtime.FILE_LIMIT + 1))
    assert workspace.read_file("solution.sh") == "keep"
    workspace.write_file("large.txt", "x" * (runtime.READ_LIMIT + 1))
    with pytest.raises(ValueError, match="read limit"):
        workspace.read_file("large.txt")


def test_total_size_and_file_count_limits_apply_before_creating_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "FILE_COUNT_LIMIT", 2)
    monkeypatch.setattr(runtime, "TOTAL_LIMIT", 8)
    workspace = runtime.Workspace(tmp_path / "workspace")
    workspace.write_file("one", "1234")
    workspace.write_file("two", "5678")
    with pytest.raises(ValueError, match="size/count"):
        workspace.write_file("three", "")
    with pytest.raises(ValueError, match="size/count"):
        workspace.write_file("one", "12345")
    assert workspace.read_file("one") == "1234"
    assert not (workspace.path / "three").exists()
