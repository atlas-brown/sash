# SaSh: Ahead-of-time Analysis of Shell Program Effects

[Artifact Available](#artifact-available-10-minutes) | [Artifact Functional](#artifact-functional-20-minutes) | [Results Reproduced](#results-reproduced-6-hours) | [Bugs in the Wild](#optional-bugs-found-in-the-wild) | [Contact Us](#contact-us)

The paper makes the following contributions:

1. **Optimistic symbolic execution engine (§3)**: A system, SaSh, that simulates shell program execution over symbolic variables, incorporating domain-specific failure modes to prune the state space.
2. **Effect and environment modeling (§4)**: A non-hierarchical filesystem model and compositional command specifications for reasoning about program-environment interactions.
3. **Abstract expansion domain (§5)**: An abstract domain capturing coarse constraints on shell expansion outcomes, enabling tractable reasoning about field splitting and related bugs.
4. **Risk-directed path prioritization (§6)**: Domain-specific optimizations that steer analysis toward program fragments likely to exhibit dangerous behavior.

SaSh is evaluated on 61 real-world shell programs containing 116 documented bugs (§7.1), compared against ShellCheck (§7.2), and characterized for performance across time budgets and 119 programs from [the Koala benchmark suite](https://kben.sh/) (§7.3). It has also uncovered 70 previously unknown bugs in open-source projects including PyTorch, Kubernetes, Next.js, and vLLM.

This artifact targets the following badges:

* [Artifact Available](#artifact-available): Reviewers confirm the artifact is publicly archived with an appropriate license (~10 min).
* [Artifact Functional](#artifact-functional): Reviewers install SaSh, verify key components, and run a minimal example (~10 min).
* [Results Reproduced](#results-reproduced): Reviewers reproduce the paper's main evaluation figures and tables (~6 h).


# Artifact Available (10 minutes)

> Reviewers confirm the artifact is publicly archived with an appropriate license

Reviewers should confirm the following:

* **Repository**: The artifact is available at [https://github.com/atlas-brown/sash](https://github.com/atlas-brown/sash) (branch `sosp26-ae` will be frozen) and archived at [Zenodo](https://zenodo.org/) (DOI TBD).
* **License**: The artifact contains an MIT license ([LICENSE](LICENSE)), allowing comparison and extension.
* **"read me" file**: The top-level [INSTRUCTIONS.md](INSTRUCTIONS.md) references the paper and provides installation instructions. The top-level [README.md](README.md) does so as well.


# Artifact Functional (10 minutes)

> Reviewers install SaSh, verify key components, and run a minimal example

> [!CAUTION] Shell programs (files with a `.sh` suffix) inside `benchmarks/bugs_and_variants` are buggy and should never be executed. Many of them perform unsafe operations that can lead to permanent data loss.


## Installation

SaSh can be installed natively or via Docker on Linux and MacOS.


### Docker Installation (recommended)

```bash
git clone https://github.com/atlas-brown/sash.git
cd sash
git checkout sosp26-ae
docker build --target dev -t sash .
docker run --rm -it -v $(pwd):/app sash /bin/bash  # You are now inside the container!
uv tool install . && uv tool update-shell; source /root/.bashrc  # Install sash as an executable command inside the container
sash --help  # Verify sash is runnable
```

For the rest of this file, instructions assume you are inside the `sash` container, and specifically the `/app` directory.


### Manual Installation

Follow the instructions in [README.md#manual-installation](README.md#manual-installation).


## Completeness

The artifact contains all code and data relevant to the paper:

| Component | Location | Paper Reference |
|-----------|----------|-----------------|
| Symbolic execution engine | [`src/sash/symb.py:1290–3200`](src/sash/symb.py) | §3 |
| Filesystem model, command specifications | [`src/sash/fs.py`](src/sash/fs.py), [`src/sash/specs.py`](src/sash/specs.py) | §4 |
| Symbolic word expansion | [`src/sash/symb.py:281–1289`](src/sash/symb.py) | §5 |
| Risk-directed exploration | [`src/sash/dfs_targeted.py`](src/sash/dfs_targeted.py), [`src/sash/symb.py:2968–3185`](src/sash/symb.py) | §6 |
| 61 real buggy programs (116 bugs) and 42 synthetic variants | [`benchmarks/bugs_and_variants/`](benchmarks/bugs_and_variants/) | §7.1, §7.2 |
| 119 Koala benchmark programs | [`benchmarks/koala/`](benchmarks/koala/) | §7.3 |
| Evaluation scripts | [`scripts/eval.sh`](scripts/eval.sh) and other scripts in the same directory | §7 |
| Bug reports filed in open-source projects | [`benchmarks/bug_reports.md`](benchmarks/bug_reports.md) | §7.4 |


## Minimal Working Example

To verify basic functionality, run SaSh on one of its benchmarks:

```bash
sash benchmarks/bugs_and_variants/sf-access_del_resource/posix.sh
```

The expected output should include a warning about an attempt to move a path that has been deleted:

```
> Line 9 (error): Command 'mv' expects paths that are directories, but one or more of the following paths might not be: /storage/sort_tv, workingfolder/
```

The script loops twice, on the first iteration deleting a directory (`workingfolder/`), and on the second iteration attempting to move it.

Running the fixed version of the script, the warning should disappear:

```
sash benchmarks/bugs_and_variants/sf-access_del_resource/fixed.sh
```

The ground truth, which includes the source of the script as well as information about ShellCheck's output on it can be found in: [`benchmarks/bugs_and_variants/sf-access_del_resource/info.yaml`](benchmarks/bugs_and_variants/sf-access_del_resource/info.yaml).


# Results Reproduced (6h, optionally 10h)

> Reviewers reproduce the paper's main evaluation figures and tables

> [!TIP]
> Precomputed results and figures can be found in [`results/precomputed`](results/precomputed/), along with the exact commands used to produce them in [`results/precomputed/_metadata`](results/precomputed/_metadata/). These results were produced by running the full evaluation on a [CloudLab c6525-25g node](www.utah.cloudlab.us/portal/show-nodetype.php?type=c6525-25g), on Ubuntu 22.04.2 LTS.


## Key Result: Bug Detection Effectiveness (§7.1, §7.2) (1 hour)

This experiment runs SaSh on all 61 buggy programs, their fixed versions, and all buggy variants mentioned in the paper. It then compares the output to the programs' ground truths. The programs, along with the ground truths, can be found in [`benchmarks/bugs_and_variants`](benchmarks/bugs_and_variants/).

To run the experiment:
```bash
./scripts/eval.sh --main
```

**Outputs**:
- `results/figures/main-eval.png` (corresponds to Fig. 10)
- `results/main-eval/results_t60.csv` (CSV with all results, used to create the aforementioned figure)

Precomputed figure, found in [`results/precomputed/figures/main-eval.png`](results/precomputed/figures/main-eval.png):

<p align="center">
  <img src="results/precomputed/figures/main-eval.png" alt="Figure 10" width="80%">
</p>


## Additional Results: Performance Analysis — Timeout Sweep (§7.3) (3.5 hours)

This experiment runs SaSh on all 61 buggy programs and their fixed versions with different timeouts (1s-100s) and three configurations: base symbolic execution, optimistic execution without risk-directed exploration, and full SaSh. The goal is to see the effect of the timeout choice and the optimizations on the bug-finding effectiveness of the system.

To run the experiment:
```bash
./scripts/eval.sh --sweep
```

**Outputs**:
- `results/figures/timeout-sweep.png` (corresponds to Fig. 11)
- `results/timeout-sweep/results_t*_*.csv` (CSV with all results, used to create the aforementioned figure)

Precomputed figure, found in [`results/precomputed/figures/timeout-sweep.png`](results/precomputed/figures/timeout-sweep.png):

<p align="center">
  <img src="results/precomputed/figures/timeout-sweep.png" alt="Figure 11" width="80%">
</p>


## Additional Results: Performance Analysis — Koala (§7.3) (1.5 hours)

This experiment runs SaSh on all 119 programs from the Koala benchmark suite to measure the time required for complete analysis (full exploration of each program), with a cap at 15 minutes.

```bash
./scripts/eval.sh --koala
```

**Outputs**:
- `results/figures/koala.png` (corresponds to the inline CDF in §7.3)
- `results/koala-eval/results_t900.csv` (CSV with all results, used to create the aforementioned figure)

Precomputed figure, found in [`results/precomputed/figures/koala.png`](results/precomputed/figures/koala.png):

<p align="center">
  <img src="results/precomputed/figures/koala.png" alt="Koala CDF" width="40%">
</p>


# Optional: Bugs Found in the Wild (4 hours)

The file [`benchmarks/bug_reports.md`](benchmarks/bug_reports.md) contains links to all 70 bugs SaSh identified in open-source projects, including PyTorch, Kubernetes, Next.js, vLLM, the P4 Compiler, and others. Reviewers may inspect the linked issues and pull requests to verify that the bugs were reported and, in many cases, confirmed and fixed by maintainers.


# Contact Us

For questions please contact <email>, or open an issue on GitHub.
