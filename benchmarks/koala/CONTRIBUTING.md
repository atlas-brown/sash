# Contributing to Koala

The Koala benchmark suite is a collaborative and evolving project. Contributions are welcome in the form of bug reports, fixes, enhancements, and new benchmarks. Below are the primary ways to contribute:

## 1. Improve Existing Benchmarks

If a benchmark fails under a specific setup, or its validation script produces incorrect results, you can help improve Koala’s robustness and portability.

### What to do

- **Open an issue** describing the problem:
  - Include the benchmark name.
  - Specify the environment (OS, shell, Docker/non-Docker, etc.).
  - Describe the point of failure (e.g., installation, input fetching, execution, validation).
  - Include relevant error messages and logs.
- **Propose a fix** via a pull request:
  - Follow the existing coding and directory conventions.
  - Keep changes minimal and scoped.
  - Ensure your changes do not break existing functionality across other benchmarks.
- If you're unsure how to fix the issue, simply open the issue and ask for guidance. Maintainers and contributors are encouraged to collaborate on solutions.

## 2. Add New Benchmarks

Koala aims to remain representative of real-world shell workloads. Contributions of new benchmarks that exercise shell usage, scripting patterns, or systems research are highly encouraged.

### Steps

<!-- - **Open an issue** first, describing your proposed benchmark:
  - What is the use case or workload?
  - Why is it relevant to shell performance or analysis?
  - Are there interesting control constructs, tool usage, or real-world datasets involved?

- **Link your pull request to the issue**:
  - This helps reviewers understand the context and motivation behind the benchmark.
  - You can do this by referencing the issue number in the PR description (e.g., “Closes #42”) or using GitHub’s auto-linking. -->

- **Submit a pull request**:
  - Structure your benchmark according to Koala’s layout in the [Instructions](INSTRUCTIONS.md#instructions) section.
  - Make sure to test the benchmark under both local (`--bare`) and Docker execution.
  - Include appropriate input sizes (`--min`, `--small`, `--full`).
  - If the benchmark requires custom validation logic, document it clearly inside `validate.sh`.

<!-- - **Run both the dynamic and static analysis tools**:
  - This is required for all new benchmarks to ensure they contribute meaningful shell behavior.
  - You can find instructions in the [Dynamic Characterization & Analysis](INSTRUCTIONS.md#dynamic-characterization--analysis) and [Static Characterization & Analysis](INSTRUCTIONS.md#static-characterization--analysis) sections.
  - Include the resulting visualizations and a short summary in the pull request description, or upload them as artifacts. -->

- **Describe and justify the addition**:
  - In your pull request, include a summary of what the benchmark does, what shell constructs it exercises, and what makes it different from or complementary to existing workloads in Koala.
  - Cite the source the benchmark is based on (e.g., a paper, system, dataset, or repository). Here is an example from the KOALA paper:  
    _Example_:  
    > **bio** consists of four programs for processing genomic and transcriptomic data. One script performs population genomics analysis [17, 41], while the other three implement key stages of the TERA-Seq platform [38] for processing and aligning RNA sequences. Inputs include a BAM genome sequencing file [79] and auxiliary data such as gene annotations, totaling 114GB for full inputs. The scripts feature fan-out/fan-in parallelism patterns, opportunities for code de-duplication, workqueue-like parallelism, and operate on large datasets. Smaller input sets (24.3GB) omit much of the optional auxiliary data, providing lighter-weight benchmarking options.
    > **References**
    > - [17] Cappellini, E., Welker, F., Pandolfi, L., et al. (2019). Early Pleistocene enamel proteome from Dmanisi resolves Stephanorhinus phylogeny. _Nature_, 574(7776), 103–107.  
    > - [38] Ibrahim, F., Oppelt, J., Maragkakis, M., & Mourelatos, Z. (2021). TERA-Seq: true end-to-end sequencing of native RNA molecules for transcriptome characterization. _Nucleic Acids Research_, 49(20), e115.  
    > - [41] Puritz, J. (2019). Bio594: Using genomic techniques to examine the evolution of populations. <https://git.io/JY6J7>  
    > - [79] The SAM/BAM Format Specification Working Group. (2024). Sequence Alignment/Map Format Specification v1.6. <https://samtools.github.io/hts-specs/SAMv1.pdf>  

## 3. Infrastructure Improvements

Enhancements to Koala's harness (`main.sh`), dynamic/static analysis tools, or Docker support are also welcome.

### Guidelines

- Maintain backward compatibility unless explicitly discussed.
- For larger changes, open an issue first to initiate design discussion.
- Follow the style and modularization conventions used in the current `.tools/` and top-level scripts.

## 4. Documentation and Usability

Improving clarity, fixing typos, or adding missing instructions to the documentation (including this very section) is a great way to contribute.

- You can directly open a pull request for small fixes.
- For larger restructuring or rewrites, please open an issue first to discuss.

---

**Note**: All contributions must comply with the licensing terms and should be submitted via GitHub using standard pull request workflows. Contributors are expected to follow good engineering practices: clear commits, reproducibility, and minimal disruption to existing functionality.

Koala welcomes contributors from academia, industry, and hobbyist communities alike. Let's advance shell benchmarking together.
