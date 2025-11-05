import difflib
import os
import shutil
import tempfile

from octoprint_plugin_tool import migrate_to_pyproject


def _get_path_for(name: str) -> str:
    return os.path.join(os.path.normpath(os.path.dirname(__file__)), "_files", name)


def _copy_contents(name: str, target: str):
    path = _get_path_for(name)
    for item in os.listdir(path):
        source = os.path.join(path, item)
        if os.path.isfile(source):
            shutil.copy2(source, target)


def _compare_contents(pathA: str, pathB: str):
    with open(pathA, newline="\n") as f:
        contentA = f.readlines()

    with open(pathB, newline="\n") as f:
        contentB = f.readlines()

    return difflib.context_diff(contentA, contentB, fromfile=pathA, tofile=pathB)


def test_setup_py_only():
    with tempfile.TemporaryDirectory() as folder:
        _copy_contents("setup-py-only", folder)

        assert migrate_to_pyproject(folder)

        assert os.path.exists(os.path.join(folder, "pyproject.toml"))
        assert os.path.exists(os.path.join(folder, "MANIFEST.in"))
        assert os.path.exists(os.path.join(folder, "Taskfile.yml"))

        delta = list(
            _compare_contents(
                os.path.join(folder, "pyproject.toml"),
                os.path.join(_get_path_for("pyproject-toml-only"), "pyproject.toml"),
            )
        )
        if delta:
            print("\n".join(delta))
        assert len(delta) == 0


def test_setup_py_and_pyproject_toml():
    with tempfile.TemporaryDirectory() as folder:
        _copy_contents("setup-py-and-pyproject-toml", folder)

        assert migrate_to_pyproject(folder)

        assert os.path.exists(os.path.join(folder, "pyproject.toml"))
        assert os.path.exists(os.path.join(folder, "MANIFEST.in"))
        assert os.path.exists(os.path.join(folder, "Taskfile.yml"))

        delta = list(
            _compare_contents(
                os.path.join(folder, "pyproject.toml"),
                os.path.join(
                    _get_path_for("setup-py-and-pyproject-toml"), "pyproject.toml"
                ),
            )
        )
        assert len(delta) > 0  # not identical to old -> updated

        delta = list(
            _compare_contents(
                os.path.join(folder, "pyproject.toml"),
                os.path.join(_get_path_for("pyproject-toml-only"), "pyproject.toml"),
            )
        )
        assert len(delta) > 0  # not identical to new -> merged


def test_pyproject_toml_only():
    with tempfile.TemporaryDirectory() as folder:
        _copy_contents("pyproject-toml-only", folder)

        assert not migrate_to_pyproject(folder)

        delta = list(
            _compare_contents(
                os.path.join(folder, "pyproject.toml"),
                os.path.join(_get_path_for("pyproject-toml-only"), "pyproject.toml"),
            )
        )
        assert len(delta) == 0  # identical to old -> nothing touched
