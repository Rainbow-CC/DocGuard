from pathlib import Path

from docguard.settings import Settings


def test_settings_reads_runtime_paths_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCGUARD_DATABASE_PATH", str(tmp_path / "tasks.sqlite3"))
    monkeypatch.setenv("DOCGUARD_RESULT_WRITE_ROOT", str(tmp_path / "results"))
    monkeypatch.setenv("DOCGUARD_RESULT_AGENT_ROOT", "/agent/results")
    monkeypatch.setenv("DOCGUARD_UPLOAD_WRITE_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("DOCGUARD_UPLOAD_AGENT_ROOT", "/agent/uploads")
    monkeypatch.setenv("DOCGUARD_PREPROCESS_COMMAND", "wsl.exe")
    monkeypatch.setenv("DOCGUARD_WSL_DISTRIBUTION", "CustomUbuntu")

    settings = Settings.from_environment()

    assert settings.database_path == tmp_path / "tasks.sqlite3"
    assert settings.result_write_root == tmp_path / "results"
    assert str(settings.result_agent_root) == "/agent/results"
    assert settings.upload_write_root == tmp_path / "uploads"
    assert str(settings.upload_agent_root) == "/agent/uploads"
    assert settings.preprocess_command == "wsl.exe"
    assert settings.wsl_distribution == "CustomUbuntu"
