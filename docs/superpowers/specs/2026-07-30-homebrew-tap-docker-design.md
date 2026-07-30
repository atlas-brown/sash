# Homebrew tap + Docker image packaging for SaSh

**Date:** 2026-07-30  
**Status:** Approved for implementation planning  
**Scope:** Distribute SaSh via a remote GitHub Homebrew tap that wraps the containerized runtime, plus CI that publishes that runtime image to GHCR.

## Problem

SaSh’s native install needs a non-trivial toolchain (autoconf/automake/libtool, a new enough C/C++ compiler, `CFLAGS=-std=gnu17`) and Python dependencies including native extensions (`libdash`) and at least historically git-sourced packages. Reproducing that faithfully in a Homebrew formula requires verifying the same dependency graph the Docker image already encapsulates. That duplication is fragile and hard to audit from outside the image.

The project already documents a Docker-based path (`Dockerfile` target `sys`, wrapper `scripts/sash-docker.sh`) precisely to avoid installing that stack on the host.

## Goals

- Let users install a `sash` command via a **remote GitHub tap** (not a local tap).
- Keep host-side packaging **thin**: wrapper + container runtime dependency only.
- Publish a **GHCR image** built from Dockerfile target **`sys`** so users need not build locally in the common case.
- On first use: **pull** the image if missing; if pull fails, **instruct** the user how to build manually.

## Non-goals

- Native Homebrew formula that builds Python/`libdash`/Shasta on the host.
- Pulling the image during `brew install` (Docker may be stopped; first-run pull is enough).
- Changing SaSh’s in-image dependency set or replacing Docker/Podman as the runtime.
- Submitting the formula to homebrew-core.

## Architecture

```text
┌─────────────────────┐     publishes      ┌──────────────────────────┐
│  atlas-brown/sash   │ ───────────────► │ ghcr.io/.../sash:<tag>   │
│  (app + Dockerfile  │   Actions CI      │  (sys target image)      │
│   + wrapper script) │                   └────────────▲─────────────┘
└─────────▲───────────┘                                │
          │ formula URL / release asset                │ pull on first run
┌─────────┴───────────┐                   ┌────────────┴─────────────┐
│  <owner>/homebrew-  │  brew install     │  host: sash wrapper      │
│  tap                │ ───────────────► │  (sash-docker.sh)        │
│  Formula/sash.rb    │                   └──────────────────────────┘
└─────────────────────┘
```

### Repository split

| Repository | Responsibility |
|------------|----------------|
| **sash** (this project) | Dockerfile (`sys`), canonical wrapper script, GHCR publish workflow, release assets the formula can download |
| **homebrew-tap** (GitHub remote tap) | `Formula/sash.rb` only — installs the wrapper, documents Docker, sets default image name |

User install:

```bash
brew tap <owner>/tap
brew install sash
sash program.sh
```

(`homebrew-tap` → Homebrew tap name `tap` under `<owner>`.)

## Design

### 1. GHCR publish workflow (sash repo)

- **Path:** `.github/workflows/publish-image.yml` (name flexible).
- **Triggers:**
  - `workflow_dispatch` (manual)
  - `push` of tags matching `v*` (e.g. `v0.1.0`)
- **Registry:** `ghcr.io`
- **Image name:** `${{ github.repository }}` (e.g. `ghcr.io/atlas-brown/sash`)
- **Build:** `docker/build-push-action` with **`target: sys`** (runtime ENTRYPOINT is `sash`; do not publish the `dev` stage as the default image).
- **Auth / permissions:** `docker/login-action` with `GITHUB_TOKEN`; job permissions at least `contents: read`, `packages: write`. Optional: attestations / `id-token: write` as in the reference workflow.
- **Tags / labels:** `docker/metadata-action` so version tags map to sensible image tags (e.g. semver from `v0.1.0`, plus SHA/branch-style tags on manual runs).

### 2. Wrapper behavior (`scripts/sash-docker.sh`)

Keep existing behavior: auto-detect Docker or Podman (`SASH_RUNTIME` override), mount existing file arguments read-only at absolute paths, pass through `SASH_DOCKER_ARGS` / `SASH_IMAGE`.

**Change:** when the configured image is not present locally:

1. Attempt `"$runtime" pull "$image"`.
2. If pull succeeds, continue with `run` as today.
3. If pull fails, print actionable guidance: build with `"$runtime" build --target sys -t sash .` (and/or set `SASH_IMAGE`), then exit non-zero.

Do **not** require a successful pull at `brew install` time.

Default image for a Homebrew install: the formula installs the canonical script under `libexec` and a one-line `bin/sash` shim that sets  
`SASH_IMAGE="${SASH_IMAGE:-ghcr.io/<owner>/sash}"` then `exec`s the script.  
That keeps local `docker build -t sash` workflows working when the script is used outside Homebrew (`SASH_IMAGE` default in-repo can stay `sash`). Override remains `SASH_IMAGE`.

### 3. Homebrew formula (`homebrew-tap` → `Formula/sash.rb`)

- **Tap:** GitHub repo named `homebrew-tap` under the same owner that publishes the image (typically the sash org/user, e.g. `atlas-brown` → `brew tap atlas-brown/tap`).
- **desc / homepage / license:** match project (homepage `https://github.com/atlas-brown/sash`; license MIT).
- **url:** versioned release asset or archive from the sash repo that includes the wrapper script (immutable release tarball + `sha256`; not a mutable branch tip).
- **depends_on:** `docker` (caveats may mention Podman + `SASH_RUNTIME`).
- **install:** place `sash-docker.sh` in `libexec`; install `bin/sash` shim with GHCR default image as above.
- **test:** `bin/sash` executable; shim/script reference docker/podman; optionally `sash --help` when Docker is available.
- **Out of scope in formula:** Python, z3, libdash, autoconf stack.

Any draft under `packaging/homebrew/` in sash is reference-only; the live formula lives in **homebrew-tap**.

### 4. Operational notes

- GHCR packages may need visibility set so anonymous `docker pull` works for public installs, or docs must mention `docker login ghcr.io`.
- Tagging a sash release (`v*`) should both cut a GitHub release asset the formula can pin and publish the matching image tag.
- Formula version bumps in **homebrew-tap** when sash releases a new wrapper/image pair.

## Success criteria

- `brew tap <owner>/tap && brew install sash` puts `sash` on `PATH`.
- With Docker running and a public (or authenticated) GHCR image: `sash --help` / analyze a file works without a local image build.
- With no registry access: failure message explains how to build `--target sys` manually.
- No Homebrew formula attempts to compile SaSh’s native/Python dependency tree on the host.

## Open points (resolved in discussion)

| Topic | Decision |
|-------|----------|
| Packaging approach | Wrapper + published image (not native) |
| Publish triggers | Manual **and** `v*` tags |
| Tap repo name | **`homebrew-tap`** |
| Image acquisition | Pull if missing; else manual-build instructions |
| When to pull | First run of wrapper, not during `brew install` |
