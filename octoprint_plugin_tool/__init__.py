import argparse
import logging
import os
import re
import sys
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from packaging.version import parse as parse_version

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python < 3.11

import tomli_w
import yaml
from validate_pyproject import api as validate_pyproject_api
from validate_pyproject import errors as validate_pyproject_errors

TASKFILE_TEMPLATE_HEADER = """
# Taskfile to be used with task: https://taskfile.dev
#
# A copy of task gets automatically installed as a "develop" dependency this plugin:
#
#   pip install .[develop]
#   go-task --list-all
#

version: "3"

env:
    LOCALES: {plugin_locales}  # list your included locales here, e.g. ["de", "fr"]
    TRANSLATIONS: "{plugin_package}/translations"  # translations folder

"""

TASKFILE_TEMPLATE_BODY = """
tasks:
    install:
        desc: Installs the plugin into the current venv
        cmds:
          - "python -m pip install -e .[develop]"

    ### Build related

    build:
        desc: Builds sdist & wheel
        cmds:
          - python -m build --sdist --wheel

    build-sdist:
        desc: Builds sdist
        cmds:
          - python -m build --sdist

    build-wheel:
        desc: Builds wheel
        cmds:
          - python -m build --wheel

    ### Translation related

    babel-new:
        desc: Create a new translation for a locale
        cmds:
          - task: babel-extract
          - pybabel init --input-file=translations/messages.pot --output-dir=translations --locale="{{ .CLI_ARGS }}"

    babel-extract:
        desc: Update pot file from source
        cmds:
          - pybabel extract --mapping-file=babel.cfg --output-file=translations/messages.pot --msgid-bugs-address=i18n@octoprint.org --copyright-holder="The OctoPrint Project" .

    babel-update:
        desc: Update translation files from pot file
        cmds:
          - for:
                var: LOCALES
            cmd: pybabel update --input-file=translations/messages.pot --output-dir=translations --locale={{ .ITEM }}

    babel-refresh:
        desc: Update translation files from source
        cmds:
          - task: babel-extract
          - task: babel-update

    babel-compile:
        desc: Compile translation files
        cmds:
          - pybabel compile --directory=translations

    babel-bundle:
        desc: Bundle translations
        preconditions:
          - test -d {{ .TRANSLATIONS }}
        cmds:
          - for:
               var: LOCALES
            cmd: |
                locale="{{ .ITEM }}"
                source="translations/${locale}"
                target="{{ .TRANSLATIONS }}/${locale}"

                [ ! -d "${target}" ] || rm -r "${target}"

                echo "Copying translations for locale ${locale} from ${source} to ${target}..."
                cp -r "${source}" "${target}"

"""

SETUP_PY_TEMPLATE = """
import setuptools

# we define the license string like this to be backwards compatible to setuptools<77
setuptools.setup(license="{plugin_license}")

"""

REQUIRED_PLUGIN_DATA = [
    "plugin_identifier",
    "plugin_package",
    "plugin_name",
    "plugin_version",
    "plugin_description",
    "plugin_author",
    "plugin_author_email",
    "plugin_url",
    "plugin_license",
    "plugin_requires",
]
EXPECTED_PLUGIN_DATA = REQUIRED_PLUGIN_DATA + [
    "additional_setup_parameters",
]


def _log(message: str, warning: bool = False, error: bool = False):
    if error:
        level = logging.ERROR
    elif warning:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.getLogger(__name__).log(level=level, msg=message)


def _get_pep508_name(name: str) -> str:
    PROJECT_NAME_VALIDATOR = re.compile(
        r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", flags=re.IGNORECASE
    )

    PROJECT_NAME_INVALID = re.compile(r"[^A-Z0-9.-]", flags=re.IGNORECASE)

    if PROJECT_NAME_VALIDATOR.match(name):
        return name

    name = PROJECT_NAME_INVALID.sub("-", name)
    if not PROJECT_NAME_VALIDATOR.match(name):
        raise ValueError(f"{name} is not PEP508 compliant")

    return name


def _get_spdx_license(license: str) -> str:
    SPDX_LICENSE_LUT = {
        "agpl-3.0": "AGPL-3.0-or-later",
        "agplv3": "AGPL-3.0-or-later",
        "agpl v3": "AGPL-3.0-or-later",
        "apache 2": "Apache-2.0",
        "apache 2.0": "Apache-2.0",
        "apache-2.0": "Apache-2.0",
        "apache license 2.0": "Apache-2.0",
        "bsd-3-clause": "BSD-3-Clause",
        "cc by-nc-sa 4.0": "CC-BY-NC-SA-4.0",
        "cc by-nd": "CC-BY-ND-4.0",
        "gnu affero general public license": "LicenseRef-AGPL",
        "gnu general public license v3.0": "GPL-3.0-or-later",
        "gnuv3": "GPL-3.0-or-later",
        "gnu v3.0": "GPL-3.0-or-later",
        "gpl-3.0 license": "GPL-3.0-or-later",
        "gplv3": "GPL-3.0-or-later",
        "mit": "MIT",
        "mit license": "MIT",
        "unlicence": "Unlicense",
    }  # extracted from plugins.octoprint.org/plugins.json on 2025-06-05

    SPDX_IDSTRING_INVALID = re.compile(r"[^A-Z0-9.-]", flags=re.IGNORECASE)

    from packaging.licenses import (
        InvalidLicenseExpression,
        canonicalize_license_expression,
    )

    try:
        return canonicalize_license_expression(
            SPDX_LICENSE_LUT.get(
                license.lower(),
                license,
            )
        )
    except InvalidLicenseExpression:
        license = SPDX_IDSTRING_INVALID.sub("-", license)
        return f"LicenseRef-{license}"


def _search_through_file(path: str, term: str, regex: bool = False):
    if regex:
        pattern = term
    else:
        pattern = re.escape(term).replace(r"\ ", " ")
    compiled = re.compile(pattern)

    with open(path, encoding="utf8", errors="replace") as f:
        for line in f:
            if term in line or compiled.match(line):
                return True
    return False


def _is_version_compatible(version, specifier):
    if not isinstance(version, Version):
        version = parse_version(version)

    if not isinstance(specifier, SpecifierSet):
        specifier = SpecifierSet(specifier)

    return version in specifier


def _extract_plugin_data_from_setup_py(
    setup_py: str, log: callable = None
) -> dict[str, Any]:
    import ast

    if log is None:
        log = _log

    log(f"Extracting plugin data from {setup_py}...")

    with open(setup_py) as f:
        contents = f.read()

    def ast_value(node) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.List):
            return [ast_value(entry) for entry in node.elts]
        elif isinstance(node, ast.Dict):
            return {
                ast_value(key): ast_value(value)
                for key, value in zip(node.keys, node.values)
            }
        else:
            raise ValueError(f"Don't know how to parse {node!r}")

    plugin_data = {}
    mod = ast.parse(contents)
    for node in mod.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in EXPECTED_PLUGIN_DATA
        ):
            field = str(node.targets[0].id)
            try:
                plugin_data[field] = ast_value(node.value)
            except ValueError:
                raise RuntimeError(
                    f"setup.py contains a non-string value for {field}, can't migrate."
                )

    if not all(key in plugin_data for key in REQUIRED_PLUGIN_DATA):
        missing = [key for key in REQUIRED_PLUGIN_DATA if key not in plugin_data]
        raise RuntimeError(
            f"setup.py does not contain all required keys, can't migrate. Required: {', '.join(REQUIRED_PLUGIN_DATA)}. MISSING: {', '.join(missing)}"
        )

    return plugin_data


def _validate_and_migrate_plugin_data(
    path: str,
    plugin_data: dict[str, Any],
    rename_package: bool = False,
    log: callable = None,
):
    if log is None:
        log = _log

    log(f"Validating and migrating plugin data for plugin in {path}...")

    # name
    try:
        plugin_data["plugin_name"] = _get_pep508_name(plugin_data["plugin_name"])
    except ValueError as err:
        raise RuntimeError(
            "Project name is not PEP508 compliant and cannot automatically "
            "be converted. Please rename your plugin manually to match PEP508. "
            "See https://packaging.python.org/en/latest/specifications/name-normalization/ for details."
        ) from err

    # package folder
    valid_package = re.compile(
        r"^([a-z0-9]|[a-z0-9][a-z0-9._]*[a-z0-9])$", flags=re.IGNORECASE
    )
    if valid_package.match(plugin_data["plugin_package"]):
        if plugin_data["plugin_package"] != plugin_data["plugin_package"].lower():
            # package has mixed case, not recommended, warn or fix
            if rename_package:
                import shutil

                log(
                    f"\tPlugin package {plugin_data['plugin_package']} contains mixed case, renaming to {plugin_data['plugin_package'].lower()}..."
                )

                current = os.path.join(path, plugin_data["plugin_package"])
                renamed = os.path.join(path, plugin_data["plugin_package"].lower())

                shutil.move(current, renamed)

                plugin_data["plugin_package"] = plugin_data["plugin_package"].lower()

            else:
                log(
                    f"\tWARNING: Plugin package {plugin_data['plugin_package']} contains mixed case, recommended to rename to {plugin_data['plugin_package'].lower()} (--rename-package)!",
                    warning=True,
                )

    else:
        raise RuntimeError(
            "Package name contains unsupported characters. Please rename your plugin's package manually to only contain a-z, 0-9 and _."
        )

    # if plugin_additional_packages or additional_setup_parameters are contained and non-empty, bail
    if any(
        len(plugin_data.get(x, []))
        for x in ("plugin_additional_packages", "additional_setup_parameters")
    ):
        raise RuntimeError(
            "Non-empty plugin_additional_packages or additional_setup_parameters detected, can't migrate."
        )

    # license
    plugin_data["plugin_license"] = _get_spdx_license(plugin_data["plugin_license"])

    # python requires
    plugin_data["plugin_python_requires"] = ">=3.7,<4"
    if (
        "additional_setup_parameters" in plugin_data
        and "python_requires" in plugin_data["additional_setup_parameters"]
    ):
        python_requires = plugin_data["additional_setup_parameters"]["python_requires"]
        try:
            SpecifierSet(python_requires)
        except InvalidSpecifier:
            log(
                "Found invalid python_requires specifier {python_requires}, falling back to >=3.7,<4",
                warning=True,
            )
        else:
            plugin_data["plugin_python_requires"] = python_requires

    # locales
    plugin_data["plugin_locales"] = []
    translation_folder = os.path.join(path, "translations")
    if os.path.isdir(translation_folder):
        import pathlib

        plugin_data["plugin_locales"] = [
            x.name for x in pathlib.Path(translation_folder).iterdir() if x.is_dir()
        ]


def _generate_pyproject_toml(
    path: str,
    plugin_data: dict[str, Any],
    enable_pep639: bool = False,
    log: callable = None,
) -> bool:
    if log is None:
        log = _log

    pyproject_toml = os.path.join(path, "pyproject.toml")
    log(f"Generating {pyproject_toml}...")

    min_setuptools = "77" if enable_pep639 else "68"

    doc = {}
    doc["build-system"] = {
        "requires": [f"setuptools>={min_setuptools}"],
        "build-backend": "setuptools.build_meta",
    }
    doc["project"] = {
        "name": plugin_data["plugin_name"],
        "version": plugin_data["plugin_version"],
        "description": plugin_data["plugin_description"],
        "authors": [
            {
                "name": plugin_data["plugin_author"],
                "email": plugin_data["plugin_author_email"],
            }
        ],
        "requires-python": plugin_data["plugin_python_requires"],
        "dependencies": plugin_data["plugin_requires"],
        "entry-points": {
            "octoprint.plugin": {
                plugin_data["plugin_identifier"]: plugin_data["plugin_package"]
            },
        },
        "urls": {"Homepage": plugin_data["plugin_url"]},
        "optional-dependencies": {"develop": ["go-task-bin"]},
    }

    if enable_pep639:
        doc["project"]["license"] = plugin_data["plugin_license"]
    else:
        doc["project"]["dynamic"] = ["license"]

    doc["tool"] = {
        "setuptools": {
            "include-package-data": True,
            "packages": {
                "find": {
                    "include": [
                        f"{plugin_data['plugin_package']}",
                        f"{plugin_data['plugin_package']}.*",
                    ]
                }
            },
        },
    }
    if "plugin_additional_data" in plugin_data:
        for item in plugin_data["plugin_additional_data"]:
            doc["tool"]["setuptools"]["packages"]["find"]["include"].append(item)

    if os.path.isfile(os.path.join(path, "README.md")):
        doc["project"]["readme"] = {
            "file": "README.md",
            "content-type": "text/markdown",
        }

    if os.path.isfile(pyproject_toml):
        # pyproject.toml already exists, so let's merge things
        from octoprint.util import dict_merge

        log("\tFound an existing pyproject.toml, merging...")
        with open(pyproject_toml, mode="rb") as f:
            data = tomllib.load(f)

        doc = dict_merge(doc, data)

    # ensure we are producing valid pyproject data
    validator = validate_pyproject_api.Validator()
    try:
        validator(doc)
    except validate_pyproject_errors.ValidationError as exc:
        raise RuntimeError(
            f"Something went wrong here, the generated pyproject.toml file didn't pass the validator: {str(exc)}"
        )

    # ensure we are producing valid toml
    try:
        tomllib.loads(tomli_w.dumps(doc))
    except tomllib.TOMLDecodeError:
        raise RuntimeError("Something went wrong here, generated TOML is invalid")

    with open(pyproject_toml, "wb") as f:
        tomli_w.dump(doc, f)

    return True


def _generate_setup_py(
    folder: str, plugin_data: dict[str, Any], log: callable = None
) -> None:
    if log is None:
        log = _log

    setup_py = os.path.join(folder, "setup.py")
    log(f"Generating {setup_py}...")

    with open(setup_py, mode="w") as f:
        f.write(SETUP_PY_TEMPLATE.format(**plugin_data))


def _generate_taskfile(
    folder: str, plugin_data: dict[str, Any], log: callable = None
) -> None:
    if log is None:
        log = _log

    taskfile = os.path.join(folder, "Taskfile.yml")
    log(f"Generating {taskfile}...")

    content = TASKFILE_TEMPLATE_HEADER.format(**plugin_data) + TASKFILE_TEMPLATE_BODY

    try:
        yaml.parse(content)
    except Exception as exc:
        raise RuntimeError(
            f"Something went wrong, the generated Taskfile yaml is invalid: {str(exc)}"
        )

    with open(taskfile, mode="w") as f:
        f.write(content)


def _update_manifest_in(
    folder: str, plugin_data: dict[str, Any], log: callable = None
) -> None:
    if log is None:
        log = _log

    manifest_in = os.path.join(folder, "MANIFEST.in")
    log(f"Updating {manifest_in} as necessary...")

    manifest_in_exists = os.path.isfile(manifest_in)
    required = [
        "include README.md",
        f"recursive-include {plugin_data['plugin_package']}/templates *",
        f"recursive-include {plugin_data['plugin_package']}/translations *",
        f"recursive-include {plugin_data['plugin_package']}/static *",
    ]

    lines = []
    for req in required:
        pattern = req.replace(".", r"\.").replace("*", r"\*")
        if not manifest_in_exists or not _search_through_file(
            manifest_in, pattern, regex=True
        ):
            log(f'\tAdding "{req}"...')
            lines.append(req)

    if lines:
        with open(manifest_in, mode="a") as f:
            f.write("\n".join(lines))


def _cleanup_setup_cfg(setup_cfg: str, log: callable = None) -> None:
    import configparser

    if log is None:
        log = _log

    if not os.path.isfile(setup_cfg):
        return True

    config = configparser.ConfigParser()
    try:
        config.read(setup_cfg)
    except configparser.ParsingError as exc:
        raise RuntimeError(f"Parsing error while reading {setup_cfg}: {exc}")

    if "bdist_wheel" in config:
        # remove `bdist_wheel.universal = 1` declaration, if it's there
        try:
            del config["bdist_wheel"]["universal"]
        except KeyError:
            pass

        if not len(config["bdist_wheel"]):
            del config["bdist_wheel"]

    if len(config):
        # there's more in here
        with open(setup_cfg, "w") as f:
            config.write(f)
        return False


def _cleanup(folder: str, enable_pep639: bool = False, log: callable = None) -> None:
    if log is None:
        log = _log

    log("Cleaning up...")

    deprecated = (
        ["setup.py", "requirements.txt"] if enable_pep639 else ["requirements.txt"]
    )
    for d in deprecated:
        path = os.path.join(folder, d)
        if os.path.isfile(path):
            log(f"\tRemoving no longer needed {path}...")
            os.remove(path)

    setup_cfg = os.path.join(folder, "setup.cfg")
    if not _cleanup_setup_cfg(setup_cfg, log=log):
        log(
            f"\tWARNING: Not removing {setup_cfg}, there might still be important tool settings in there. MANUAL MERGE REQUIRED!",
            warning=True,
        )
    elif os.path.isfile(setup_cfg):
        log(f"\tRemoving no longer needed {setup_cfg}...")
        os.remove(setup_cfg)


def migrate_to_pyproject(
    path, force: bool = False, rename: bool = False, log: callable = None
):
    if log is None:
        log = _log

    setup_py = os.path.join(path, "setup.py")
    has_setup_py = os.path.isfile(setup_py)
    if not has_setup_py:
        log("No setup.py found", warning=True)
        return False

    if not force and not _search_through_file(setup_py, "import octoprint_setuptools"):
        log(
            "This doesn't look like a plugin based on OctoPrint's setup.py template",
            warning=True,
        )
        return False

    try:
        plugin_data = _extract_plugin_data_from_setup_py(setup_py, log=log)
        _validate_and_migrate_plugin_data(
            path, plugin_data, rename_package=rename, log=log
        )

        python_requires = plugin_data["plugin_python_requires"]
        enable_pep639 = not _is_version_compatible(
            "3.7", python_requires
        ) and not _is_version_compatible(
            "3.8", python_requires
        )  # only go full PEP639 if the plugin doesn't support Python 3.7 & 3.8!

        if enable_pep639:
            log("Plugin's python requirements indicate PEP639 compatibility")
        else:
            log("Plugin still supports EOL Python 3.7 or 3.8, not enabling PEP639")

        _generate_pyproject_toml(
            path, plugin_data, enable_pep639=enable_pep639, log=log
        )
        if not enable_pep639:
            _generate_setup_py(path, plugin_data, log=log)
        _generate_taskfile(path, plugin_data, log=log)
        _update_manifest_in(path, plugin_data, log=log)
        _cleanup(path, enable_pep639=enable_pep639, log=log)

        return True
    except RuntimeError as exc:
        log(f"Error during migration: {str(exc)}", error=True)
        return False


def main():
    NO_COLOR = os.environ.get("NO_COLOR") == "1"

    BASE = "\033["

    class TextColors:
        RED = BASE + "31m"
        GREEN = BASE + "32m"
        YELLOW = BASE + "33m"
        WHITE = BASE + "37m"
        DEFAULT = BASE + "39m"

    class TextStyles:
        BRIGHT = BASE + "1m"
        NORMAL = BASE + "22m"

    def _ansi_support() -> bool:
        for handle in (sys.stdout, sys.stderr):
            if (
                hasattr(handle, "isatty")
                and handle.isatty()
                and sys.platform != "win32"
            ) or os.environ.get("TERM") == "ANSI":
                continue
            return False
        return True

    _ansi_supported = _ansi_support()

    def _print(*msg, color=TextColors.DEFAULT, style=None, end="\n", stream=sys.stdout):
        if NO_COLOR or not _ansi_supported:
            print(*msg, end=end, file=stream)
            return

        if not style:
            print(
                color, " ".join(msg), TextColors.DEFAULT, sep="", end=end, file=stream
            )
        else:
            print(
                color,
                style,
                " ".join(msg),
                TextColors.DEFAULT,
                TextStyles.NORMAL,
                sep="",
                end=end,
                file=stream,
            )

    def log(msg: str, warning: bool = False, error: bool = False):
        color = TextColors.DEFAULT
        if error:
            color = TextColors.RED
        elif warning:
            color = TextColors.YELLOW

        _print(msg, color=color)

    parser = argparse.ArgumentParser(
        prog="octoplugin-tool",
        description="A CLI tool to help with maintaining OctoPrint Plugins",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        dest="verbosity",
        action="count",
        help="increase logging verbosity",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    pyproject = subparsers.add_parser(
        "to-pyproject",
        help="Migrate legacy setup.py based OctoPrint plugin to modern pyproject.toml based packaging",
    )
    pyproject.add_argument(
        "--path",
        help="Path of the local plugin development folder to migrate, if other than cwd",
    )
    pyproject.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Force migration, even if setup.py looks wrong",
    )
    pyproject.add_argument(
        "--rename-package",
        dest="rename_package",
        action="store_true",
        help="Automatically rename package to recommended naming scheme",
    )

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    if args.subcommand == "to-pyproject":
        path = args.path
        if not path:
            path = os.getcwd()
        path = os.path.normpath(path)

        _print(f"Attempting to migrate plugin in {path}")

        if migrate_to_pyproject(
            path, force=args.force, rename=args.rename_package, log=log
        ):
            _print("... done!", color=TextColors.GREEN)
            _print()
            _print(
                "PLEASE REVIEW THE CHANGES THOROUGHLY AND MAKE SURE TO TEST YOUR PLUGIN AND ITS INSTALLATION!",
                color=TextColors.WHITE,
                style=TextStyles.BRIGHT,
            )

        else:
            _print("... failed!", color=TextColors.RED)


if __name__ == "__main__":
    main()
