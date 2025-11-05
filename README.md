# octoprint-plugin-tool

A CLI tool (and library) to help with maintaining OctoPrint Plugins.

## Installation

```
pip install octoprint-plugin-tool
```

`octoprint-plugin-tool` requires Python 3.9 or later.

## Usage

### As a Python library

```python
import sys

from octoprint_plugin_tool import migrate_to_pyproject

def log(message, warning: bool = False, error: bool = False):
  if warning:
    message = f"[WARNING] {message}"
  print(message, file=sys.stderr if error else sys.stdout)

if migrate_to_pyproject("/path/to/plugin", force=False, rename=True):
  print("Yay!")
else:
  print("Nay!", file=sys.stderr)
```

### As a command line tool

<!--INSERT:help-->

```
$ octoprint-plugin-tool
usage: octoplugin-tool [-h] [--verbose] {to-pyproject} ...

A CLI tool to help with maintaining OctoPrint Plugins

positional arguments:
  {to-pyproject}
    to-pyproject  Migrate legacy setup.py based OctoPrint plugin to modern
                  pyproject.toml based packaging

options:
  -h, --help      show this help message and exit
  --verbose, -v   increase logging verbosity
```

<!--/INSERT:help-->

#### `to-pyproject`

This subcommand allows you to migrate most `setup.py` based OctoPrint plugins generated from
OctoPrint's plugin template until June 2025 (e.g. through `octoprint dev plugin:new`) to
more modern `pyproject.toml` based tooling.

Current limitations include the use of any non-string values for the various `plugin_` properties,
the use of `plugin_additional_packages` or `additional_setup_parameters`, or non-compliant package
or identifier names.

The tool will bail if it detects any issues and give you an error indicating what's wrong.

<!--INSERT:to-pyproject-->

```
$ octoprint-plugin-tool to-pyproject --help
usage: octoplugin-tool to-pyproject [-h] [--path PATH] [--force]
                                    [--rename-package]

options:
  -h, --help        show this help message and exit
  --path PATH       Path of the local plugin development folder to migrate, if
                    other than cwd
  --force           Force migration, even if setup.py looks wrong
  --rename-package  Automatically rename package to recommended naming scheme
```

<!--/INSERT:to-pyproject-->

#### Example

<!--INSERT:example-->

```
$ octoprint-plugin-tool to-pyproject --path OctoPrint-RequestSpinner/
Attempting to migrate plugin in OctoPrint-RequestSpinner
Extracting plugin data from OctoPrint-RequestSpinner/setup.py...
Validating and migrating plugin data for plugin in OctoPrint-RequestSpinner...
Plugin still supports EOL Python 3.7 or 3.8, not enabling PEP639
Generating OctoPrint-RequestSpinner/pyproject.toml...
Generating OctoPrint-RequestSpinner/setup.py...
Generating OctoPrint-RequestSpinner/Taskfile.yml...
Updating OctoPrint-RequestSpinner/MANIFEST.in as necessary...
        Adding "recursive-include octoprint_requestspinner/templates *"...
        Adding "recursive-include octoprint_requestspinner/translations *"...
        Adding "recursive-include octoprint_requestspinner/static *"...
Cleaning up...
        Removing no longer needed OctoPrint-RequestSpinner/requirements.txt...
... done!

PLEASE REVIEW THE CHANGES THOROUGHLY AND MAKE SURE TO TEST YOUR PLUGIN AND ITS INSTALLATION!
```

<!--/INSERT:example-->
