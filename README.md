# SaSh

A static analysis tool for the Unix shell, based on symbolic execution.

## Installation

### Native (Linux)

Make sure you have the following installed:
* `git`
* `make`
* `automake`
* `autoconf`
* `libtool`
* `g++-13` or `clang-17` (or newer)
* [`uv`](https://github.com/astral-sh/uv) (recommended) or `pipx`

You already have `g++-13` or `clang-17` if you are on Debian 13, Ubuntu 23, or newer.

Then, run:
```bash
uv tool install git+https://github.com/atlas-brown/resash.git
uv tool update-shell  # If PATH needs to be updated
```

Or:

```bash
pipx install git+https://github.com/atlas-brown/resash.git
pipx ensurepath  # If PATH needs to be updated
```

### Containerized (Linux, MacOS)

Unfortunately some of the dependencies don't build on MacOS, so the best option for now is using a Docker image.

To install:

```bash
git clone https://github.com/atlas-brown/resash.git
docker build --target sys -t sash ./resash
docker run --rm sash --help  # Should output a help message
rm -rf ./resash
```

**(IMPORTANT)** To run:

```bash
# SaSh needs to be able to read files on the host machine, so it must be run as:
docker run --rm -v "$(pwd)":/ws -w /ws sash file.sh
# Thus, it's recommended to create an alias or a function:
echo "alias sash='docker run --rm -v \"\$(pwd)\":/ws -w /ws sash'" >> ~/.bashrc  # Or equivalent rc file
# If you want to pause/resume execution using CRIU, you also need to add '--privileged' to the aliased invocation
```

## Contributing

### Containerized Development (Linux, MacOS)

The project provides a devcontainer file for containerized development (found in `/.devcontainer`).
Additionally the Dockerfile provides an additional target for development (`dev`), which does not copy the project files into the container to allow for mounting.

```bash
docker build --target dev -t sash-dev .
docker run --rm -it -v $(pwd):/app sash-dev /bin/bash
# Again, remember to add '--privileged' if you need to use CRIU
```

### Testing

This project uses [`pytest`](https://docs.pytest.org/).
To run all tests, use `uv run pytest`

To ensure correct [test discovery](https://docs.pytest.org/en/7.1.x/explanation/goodpractices.html#conventions-for-python-test-discovery) when writing new tests:
* Test files should be named with the prefix `test_` (e.g., `test_example.py`).
* Test functions should also start with `test_` (e.g., `def test_example(): ...`).
