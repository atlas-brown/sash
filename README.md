# (Re)SaSh

A static analysis tool for the shell, based on symbolic execution.

## Getting Started

### Development Using Docker Containers (*Recommended*)

#### Prerequisites

* [`Docker`](https://docs.docker.com/get-docker/)

#### Installation

```bash
git clone https://github.com/atlas-brown/resash.git
cd resash
docker build -t resash . # This might take a while, but only ever needs to be executed once
docker run --rm --privileged -itv $(pwd):/home/sash resash /bin/bash
# You are now inside the container!
# All changes you make locally will be immediately reflected in the container!
# See https://docs.docker.com/get-started/ if you've never used Docker before
```

Note: The `--privileged` flag is required for CRIU to work.

#### Verifying Installation

```bash
# Run these inside the container!
uv run verify_installation # Verify you can run the project
uv run pytest # Verify you can run the tests
# Both commands should terminate without errors!
```

### Development Using Your Local Environment

#### Prerequisites

* [`uv`](https://github.com/astral-sh/uv)
* `automake`, `autoconf`, `libtool` (required by the [`libdash`](https://github.com/binpash/libdash) module)
    * **Linux**: `apt install automake autoconf libtool`
    * **macOS** (with `Homebrew`): `brew install automake autoconf libtool`

#### Installation

```bash
git clone https://github.com/atlas-brown/resash.git
cd resash
uv sync
```

#### Verifying the Installation

```bash
uv run pytest # Verify you can run the tests (also verifies a correct installation)
# The command should terminate without errors!
```

## Guidelines

### Code Style

* This project uses [`Black`](https://black.readthedocs.io/) for code formatting.
  ```bash
  uv run black .
  ```
* This project uses [`EditorConfig`](https://editorconfig.org/) to help enforce consistent coding styles across editors and IDEs.
* **It’s recommended to integrate `Black` and `EditorConfig` into your development environment** for automatic formatting and style consistency.
  * `VSCode`: configurations can be found in the `.vscode` folder. Remove the `.default` suffix to use them.

### Testing

* This project uses [`pytest`](https://docs.pytest.org/).
* To run all tests, use `uv run pytest`
* To ensure [test discovery](https://docs.pytest.org/en/7.1.x/explanation/goodpractices.html#conventions-for-python-test-discovery) works correctly:
  * Test files should be named with the prefix `test_` (e.g., `test_example.py`).
  * Test functions should also start with `test_`.

### Contributing

1. Create a new branch (`git checkout -b branch-name`), **do NOT push directly onto the main branch**
2. Open a pull request when you think your changes are ready to be merged
