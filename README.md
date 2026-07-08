# SaSh: Ahead-of-time Analysis of Shell Program Effects

Quick jump: [Examples](#examples) | [Installation](#installation) | [Contributing](#contributing)

This is the artifact for paper #133 Ahead-of-time Analysis of Shell Program Effects. It contains all code, data, and experiment scripts to support the paper's contributions.

> [!NOTE]
> **Artifact evaluation:**
> To evaluate the system of our SOSP'26 paper titled *"Ahead-of-time Analysis of Shell Program Effects"*, jump straight into [INSTRUCTIONS.md](INSTRUCTIONS.md).

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


### Empty variable leading to deletion of critical paths

Consider a script that captures the output of a command and later uses that value to clean up a directory:

```bash
#!/bin/sh
ROOT="$(some-command)"
rm -rf "$ROOT/"*
```

If `some-command` fails or produces no output, `$ROOT` is empty. The shell then expands `"$ROOT/"*` to `/*`, and `rm -rf` deletes every user-writable file on the system.

SaSh detects this ahead of time:

```
> Line 3 (error): Word splitting or empty variable could lead to deletion of system file /*
```

This is the same class of bug responsible for the 2015 Steam updater incident[^steam]. Detection requires reasoning about whether a variable can be empty and tracing that value to a destructive command.

[^steam]: [https://github.com/ValveSoftware/steam-for-linux/issues/3671](https://github.com/ValveSoftware/steam-for-linux/issues/3671)


### Conditional data loss from moving files

This script moves two files to the same destination:

```bash
#!/bin/sh
mv a target
mv b target
```

If `target` is a directory, both files end up inside it and the operation is safe. If `target` is a regular file, the first `mv` renames `a` to `target`, and the second `mv` renames `b` to `target`, silently overwriting `a`.

SaSh warns about the risk, but notes the condition under which it applies:

```
> Line 3 (error): Command 'mv' deletes the following paths, one of which has not been read, potentially causing loss of data: target
    but only if unknown paths are assumed to be files
```

The qualification is important: SaSh distinguishes between bugs that will occur in all environments and those that depend on the state of the filesystem.


## Installation

SaSh can be installed natively on Linux and MacOS, or used through Docker.

All dependencies of SaSh are listed in the [Dockerfile](Dockerfile) and [pyproject.toml](pyproject.toml).
The following installation instructions make use of these configurations as appropriate.

### Manual Installation

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

Obtain the repository:
```bash
git clone https://github.com/atlas-brown/sash.git
cd sash
git checkout sosp26-ae
```

Then, run:
```bash
CFLAGS="-std=gnu17" uv tool install git+https://github.com/atlas-brown/sash.git@sosp26-ae
uv tool update-shell  # If PATH needs to be updated
```

Or:

```bash
CFLAGS="-std=gnu17" pipx install git+https://github.com/atlas-brown/sash.git@sosp26-ae
pipx ensurepath  # If PATH needs to be updated
```


### Docker Installation

If you want to avoid installing a bunch of dependencies, you can use SaSh through Docker.

To install:

```bash
git clone https://github.com/atlas-brown/sash.git
cd sash
git checkout sosp26-ae
docker build -t sash .
docker run --rm sash --help  # Should output a help message
# Install the wrapper script (see below) onto your PATH, then clean up:
mkdir -p ~/.local/bin
install -m 0755 ./sash/scripts/sash-docker.sh ~/.local/bin/sash
cd ..
rm -rf ./sash
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



# Contact

For questions please contact `lukas_lazarek@brown.edu`, or open an issue on GitHub.
