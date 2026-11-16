import pytest

from cfe.env import (
    AutoConfig,
    Choices,
    Config,
    Csv,
    RepositoryEnv,
    RepositoryIni,
    UndefinedValueError,
)


def write(path, text):
    path.write_text(text)
    return str(path)


def test_env_file_parsing_and_precedence(tmp_path, monkeypatch):
    src = write(
        tmp_path / ".env",
        "# comment\nA=1\nexport B='two words'\nC=\"x=y\"\nEMPTY=\nnot a line\n",
    )
    config = Config(RepositoryEnv(src))
    assert config("A", cast=int) == 1
    assert config("B") == "two words"
    assert config("C") == "x=y"
    assert config("EMPTY", cast=bool) is False
    monkeypatch.setenv("A", "9")
    assert config("A", cast=int) == 9
    with pytest.raises(UndefinedValueError):
        config("MISSING")
    assert config("MISSING", default="d") == "d"


def test_mapping_view(tmp_path, monkeypatch):
    monkeypatch.delenv("ONLY_FILE", raising=False)
    config = Config(RepositoryEnv(write(tmp_path / ".env", "ONLY_FILE=f\n")))
    env = config.as_mapping()
    assert env["ONLY_FILE"] == "f"
    assert "ONLY_FILE" in env and "NOPE_X" not in env
    assert env.get("NOPE_X") is None
    assert "ONLY_FILE" in list(env)


def test_ini_repository(tmp_path):
    config = Config(RepositoryIni(write(tmp_path / "settings.ini", "[settings]\nDEBUG=true\n")))
    assert config("DEBUG", cast=bool) is True


def test_autoconfig_walks_up(tmp_path, monkeypatch):
    monkeypatch.delenv("FOUND", raising=False)
    write(tmp_path / ".env", "FOUND=yes\n")
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    assert AutoConfig(child)("FOUND") == "yes"
    assert AutoConfig(child).loaded().lookup("FOUND") == "yes"


def test_csv_and_choices():
    assert Csv()("a, b,c") == ["a", "b", "c"]
    assert Csv(cast=int)("1,2") == [1, 2]
    assert Csv()(None) == []
    assert Choices(["a", "b"])("a") == "a"
    with pytest.raises(ValueError):
        Choices(["a"])("z")
