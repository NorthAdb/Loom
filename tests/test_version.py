import loom
from loom import cli


def test_version_prints_package_version(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == loom.__version__
