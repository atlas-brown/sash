# (Re)SaSh

A static analysis tool for the shell, based on symbolic execution.

## Getting Started

### Prerequisites

* [`uv`](https://github.com/astral-sh/uv)
* `automake`, `autoconf`, `libtool` (required by the [`libdash`](https://github.com/binpash/libdash) module)
    * **Linux**: `apt install automake autoconf libtool`
    * **macOS** (with `Homebrew`): brew install automake autoconf libtool`
* [`Docker`](https://docs.docker.com/get-docker/) (optional but recommended, for containerized development and testing)

### Installation

```bash
git clone https://github.com/atlas-brown/resash.git
cd resash
uv sync
```

### Verifying the Installation

```bash
uv run example # Verify the project runs
uv run pytest  # Verify the tests run
# Both commands should terminate without errors!
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
* To ensure [test discovery](https://docs.pytest.org/en/7.1.x/explanation/goodpractices.html#conventions-for-python-test-discovery) works correctly:
  * Test files should be named with the prefix `test_` (e.g., `test_example.py`).
  * Test functions should also start with `test_`.

### Using Docker

* A `Dockerfile` is provided for running or developing the application inside a container.
  ```bash
  docker build -t resash .
  docker run resash
  docker run -it resash # To make container interactive
  ```

### Contributing

1. Create a *new* branch (`git checkout -b branch-name`)
2. Commit your changes (`git commit -m "Add feature"`)
3. Push to the branch (`git push branch-name`)
4. Open a pull request when you think your changes are ready to be merged
5. **Do NOT push directly onto the main branch!**
