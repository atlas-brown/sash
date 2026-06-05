# SaSh: Ahead-of-time Analysis of Shell Program Effects

A static analysis tool for the Unix shell, based on symbolic execution.

> [!NOTE]
> If you're interested in evaluating the SaSh artifact, read [INSTRUCTIONS.md](INSTRUCTIONS.md) first.

## Installation

### Native (Linux, MacOS)

Make sure you have the following installed:
* `git`
* `make`
* `automake`
* `autoconf`
* `libtool`
* `g++-13` or `clang-17` (or newer)
* [`uv`](https://github.com/astral-sh/uv) (recommended) or `pipx`

You already have `g++-13` or `clang-17` if you are on Debian 13, Ubuntu 23, or newer.
You probably already have `clang-17` if you've installed the [`xcode` command line tools](https://developer.apple.com/documentation/xcode/command-line-tools).

Then, run:
```bash
CFLAGS="-std=gnu17" uv tool install git+https://github.com/atlas-brown/resash.git
uv tool update-shell  # If PATH needs to be updated
```

Or:

```bash
CFLAGS="-std=gnu17" pipx install git+https://github.com/atlas-brown/resash.git
pipx ensurepath  # If PATH needs to be updated
```

### Containerized (Linux, MacOS)

If you want to avoid installing a bunch of dependencies, you can use SaSh through Docker.

To install:

```bash
git clone https://github.com/atlas-brown/resash.git
docker build --target sys -t sash ./resash
docker run --rm sash --help  # Should output a help message
rm -rf ./resash
```

> [!IMPORTANT]
> To run:
>
> ```bash
> # SaSh needs to be able to read files on the host machine, so it must be run as:
> docker run --rm -v "$(pwd)":/ws -w /ws sash file.sh
> # Thus, it's recommended to create an alias or a function:
> echo "alias sash='docker run --rm -v \"\$(pwd)\":/ws -w /ws sash'" >> ~/.bashrc  # Or equivalent rc file
> # If you want to pause/resume execution using CRIU, you also need to add '--privileged' to the aliased invocation
> ```

## Contributing

### Containerized Development (Linux, MacOS)

The project provides [a configuration file for containerized development](.devcontainer/devcontainer.json).
Additionally, the Dockerfile provides an additional target for development (`dev`), which does not copy the project files into the container, to allow for mounting.

```bash
docker build --target dev -t sash-dev .
docker run --rm -it -v $(pwd):/app sash-dev /bin/bash
# Again, remember to add '--privileged' if you need to use CRIU
```

### Testing

This project uses [`pytest`](https://docs.pytest.org/).
To run all tests, use `uv run pytest`.

To ensure correct [test discovery](https://docs.pytest.org/en/7.1.x/explanation/goodpractices.html#conventions-for-python-test-discovery) when writing new tests:
* Test files should be named with the prefix `test_` (e.g., `test_example.py`).
* Test functions should also start with `test_` (e.g., `def test_example(): ...`).
