import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

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


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    load_dotenv(tmp_path / "nope.env")
