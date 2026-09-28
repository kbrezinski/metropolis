from metropolis import paths


def test_base_dir_is_repository_root():
    assert (paths._BASE_DIR / "pyproject.toml").is_file()
    assert paths.DATA_DIR == paths._BASE_DIR / "data"


def test_ensure_directories_creates_all_output_dirs(monkeypatch, tmp_path):
    names = (
        "RAW_DATA_DIR",
        "PROCESSED_DATA_DIR",
        "BINARIES_DIR",
        "CHECKPOINTS_DIR",
        "LOGS_DIR",
        "CACHE_DIR",
        "OUTPUTS_DIR",
    )
    for name in names:
        monkeypatch.setattr(paths, name, tmp_path / name.lower())

    paths.ensure_directories()

    for name in names:
        assert (tmp_path / name.lower()).is_dir()
