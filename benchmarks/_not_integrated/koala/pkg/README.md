## pkg

This benchmark reads a list of package names, fetches the AUR package build scripts (`PKGBUILD`) and builds them using the `makedeb` tool.
It also performs inference of security permissions on a number of npm packages.

### Inputs

- `inputs/packages`: A newline-delimited list of package names.

### Validation

For the `pacaur` part of the benchmark, correctness is determined by checking whether the log file contains the string "Finished making".
If the string is found for all packages, the run is considered successful.

For the permission inference part, correctness is determined by that the inferred permissions match the pre-computed ones through their hash.

### proginf

This benchmark uses the `mir-sa` static analyzer to infer program-level permissions for a list of npm packages.  
It reads package names from an index file (`inputs/index.txt`), and for each package, it runs the `mir-sa` analysis tool inside the corresponding `node_modules/<package>` directory.  
Each inference result is stored as a log file in `outputs/<number>.log`, where `<number>` is the line number in the input index.  
The goal is to determine potential runtime permissions or system accesses (e.g., network, file, subprocess) statically from the JavaScript source code.

### Inputs

- `inputs/index.txt`: A newline-delimited list of npm package names for the permission inference step.
- `inputs/node_modules/<package>`: The installed source code of the npm packages to be analyzed.

### Validation

For the `proginf` part of the benchmark, correctness is determined by computing `md5` hashes of all its output files and comparing them against a precomputed set of hashes located at `hashes/outputs.hashes`. If any mismatch is found during the comparison using `diff`, the benchmark is marked as failed.
