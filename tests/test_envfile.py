import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import envfile
from envfile import load_dotenv


def test_load_dotenv_trims_spaces_and_quotes_and_keeps_existing(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nA_KEY= abc \nB_KEY=\"q v\"\nexport C_KEY='x'\nEMPTY=\nD_KEY=new\n")
    for k in ("A_KEY", "B_KEY", "C_KEY", "EMPTY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("D_KEY", "from-shell")
    load_dotenv(f)
    import os
    assert os.environ["A_KEY"] == "abc" and os.environ["B_KEY"] == "q v"
    assert os.environ["C_KEY"] == "x" and "EMPTY" not in os.environ
    assert os.environ["D_KEY"] == "from-shell"
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(k)


def test_load_dotenv_overrides_empty_shell_variable(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("E_KEY=real\n")
    monkeypatch.setenv("E_KEY", "")           # e.g. left behind by a broken `export $(...)`
    load_dotenv(f)
    import os
    assert os.environ["E_KEY"] == "real"


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    load_dotenv(tmp_path / "nope.env")


# ---- Streamlit Cloud: keys arrive in st.secrets, not in the environment -----------------
def test_secrets_fill_the_environment_so_config_sees_them(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    envfile.load_secrets({"EIA_API_KEY": "from-secrets"})
    assert os.environ["EIA_API_KEY"] == "from-secrets"


def test_a_real_environment_variable_still_wins(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "from-shell")
    envfile.load_secrets({"EIA_API_KEY": "from-secrets"})
    assert os.environ["EIA_API_KEY"] == "from-shell"


def test_only_plain_strings_are_copied(monkeypatch):
    monkeypatch.delenv("SECTION", raising=False)
    monkeypatch.delenv("EMPTY_ONE", raising=False)
    monkeypatch.delenv("NUMBER", raising=False)
    envfile.load_secrets({"SECTION": {"nested": "x"}, "EMPTY_ONE": "  ", "NUMBER": 7,
                          "GOOD": "yes"})
    assert "SECTION" not in os.environ and "EMPTY_ONE" not in os.environ
    assert "NUMBER" not in os.environ and os.environ["GOOD"] == "yes"


def test_no_secrets_file_is_not_an_error():
    class Exploding:
        def keys(self):
            raise FileNotFoundError("no secrets.toml")

    envfile.load_secrets(Exploding())   # running locally without secrets must not crash
    envfile.load_secrets(None)          # and neither must a missing Streamlit
