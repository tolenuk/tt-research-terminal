import pytest

from obb_terminal.config import AiConfig, AppConfig, config_dir, load_config


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "none.toml") == AppConfig()
    assert AiConfig() == AiConfig("claude-opus-5-5", "medium", 300.0, None)


def test_reads_ai_section(tmp_path):
    path = write(tmp_path, '[ai]\nmodel = "claude-sonnet-5-5"\neffort = "low"\ntimeout_s = 120\nclaude_path = "/opt/claude"\n')
    assert load_config(path).ai == AiConfig("claude-sonnet-5-5", "low", 120.0, "/opt/claude")


@pytest.mark.parametrize(("value", "expected"), [(5, 30.0), (5000, 900.0), ('"x"', 300.0), ("true", 300.0)])
def test_timeout_clamped_or_defaulted(tmp_path, value, expected):
    assert load_config(write(tmp_path, f"[ai]\ntimeout_s = {value}\n")).ai.timeout_s == expected


@pytest.mark.parametrize("text", ['[ai]\neffort = "turbo"\n', '[ai]\nmodel = "--dangerous"\n', '[ai]\nmodel = 5\n'])
def test_invalid_values_fall_back(tmp_path, text):
    assert load_config(write(tmp_path, text)).ai == AiConfig()


def test_broken_toml_gives_defaults(tmp_path):
    assert load_config(write(tmp_path, "[ai\nmodel=")) == AppConfig()


def test_config_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "obb-terminal"
