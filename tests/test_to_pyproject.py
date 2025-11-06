import os
import shutil
import tempfile
from pathlib import Path

import pytest

from octoprint_plugin_tool import migrate_to_pyproject


def _get_path_for(name: str) -> Path:
    return Path(
        os.path.join(os.path.normpath(os.path.dirname(__file__)), "_files", name)
    )


def _copy_all_to(source: Path, destination: Path):
    for item in source.iterdir():
        shutil.copy(item, destination)


def _path_contents(path: Path) -> dict[str, str]:
    contents = {}
    for item in path.iterdir():
        if item.is_dir():
            for name, data in _path_contents(item):
                contents[f"{item.name}/{name}"] = data
        else:
            contents[item.name] = item.read_text()
    return contents


@pytest.mark.parametrize(
    "folder, expected_return",
    [
        ("setup-py-only", True),
        ("setup-py-and-pyproject-toml", True),
        ("pyproject-toml-only", False),
    ],
)
def test_setup_py_only(folder: str, expected_return: bool):
    with tempfile.TemporaryDirectory() as work:
        folder_path = _get_path_for(folder)
        input_path = folder_path / "input"

        if not expected_return:
            expected_path = input_path
        else:
            expected_path = folder_path / "expected"

        work_path = Path(work)

        _copy_all_to(input_path, work_path)

        assert migrate_to_pyproject(work) == expected_return

        expected_contents = _path_contents(expected_path)
        actual_contents = _path_contents(work_path)

        assert expected_contents == actual_contents
