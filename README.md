# SaSh: Ahead-of-time Analysis of Shell Program Effects

[Examples](#examples) | [Installation](#installation) | [Contributing](#contributing)
> [!NOTE]
> If you're interested in evaluating the SaSh artifact, read [INSTRUCTIONS.md](INSTRUCTIONS.md) first.

SaSh is a static analysis tool for the Unix shell, using symbolic execution to find bugs in shell programs.
It currently supports the set of features and syntax defined by the POSIX standard.
After installation, running SaSh is as simple as:
```bash
sash program.sh
> ...
> Line 359 (error): Word splitting or empty variable could lead to deletion of system file /*
> ...
```


## Examples

TBA


## Installation

SaSh can be installed natively on Linux and MacOS, or used through Docker.

### Native

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


### Containerized

If you want to avoid installing a bunch of dependencies, you can use SaSh through Docker.

To install:

```bash
git clone https://github.com/atlas-brown/resash.git
docker build -t sash ./resash
docker run --rm sash --help  # Should output a help message
# Install the wrapper script (see below) onto your PATH, then clean up:
mkdir -p ~/.local/bin
install -m 0755 ./resash/scripts/sash-docker.sh ~/.local/bin/sash
rm -rf ./resash
```

> [!IMPORTANT]
> The `sash` image reads files from the host, so the file to be analyzed
> must be mounted into the container. The `sash-docker.sh` wrapper installed above
> handles this for you: it mounts each file argument (read-only) into the
> container at its own absolute path and passes everything else through to SaSh,
> so you can just run `sash file.sh` from anywhere. It runs under either Docker or
> Podman, auto-detecting whichever is installed (override with `SASH_RUNTIME`).
>
> ```bash
> # To pass extra `docker run` flags (e.g. '--privileged' for pausing/resuming
> # execution via CRIU), set SASH_DOCKER_ARGS:
> SASH_DOCKER_ARGS=--privileged sash file.sh
> # To run a differently-tagged image, set SASH_IMAGE (default: sash).
>
> # Without the wrapper, you can mount manually, but then SaSh can only see files
> # under the mounted directory:
> docker run --rm -v "$(pwd)":/ws -w /ws sash file.sh
> ```


## Contributing

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
