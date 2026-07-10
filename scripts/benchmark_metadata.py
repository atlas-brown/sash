from pathlib import Path
import re


BENCHMARK_DESCRIPTIONS = {
    "bugs_and_variants/hp-steam": "Steam updater",
    "bugs_and_variants/hp-bumblebee": "NVIDIA driver installer",
    "bugs_and_variants/hp-itunes": "iTunes updater",
    "bugs_and_variants/hp-squid": "Squid init script",
    "bugs_and_variants/hp-n": "Node.js version manager",
    "bugs_and_variants/hp-backup_manager": "Ubuntu backup manager",
    "bugs_and_variants/m1-const_loop": "DigitalOcean snapshot",
    "bugs_and_variants/m1-loop_once-useless_test": "AutoTest config rename",
    "bugs_and_variants/m1-unset_var_1": "OhMyZsh update script",
    "bugs_and_variants/m2-rm_root": "MongoDB backup script",
    "bugs_and_variants/wf-rm_root_2": "AIX server data gather",
    "bugs_and_variants/c-debootstrap": "Debian debootstrap",
    "bugs_and_variants/sf-overwrite_file": "SLURM cluster overwrite",
    "bugs_and_variants/c-const_cond": "Git config check",
    "bugs_and_variants/c-debootstrap_2": "Debian debootstrap 2",
    "bugs_and_variants/c-ignored_command_v": "Zsh installer check",
    "bugs_and_variants/c-makefile": "Camlp5 install wrapper",
    "bugs_and_variants/c-cp_nonexistent": "TIB deploy script",
    "bugs_and_variants/c-unset_func": "Git version check",
    "bugs_and_variants/c-unset_var_2": "VSCode patch helper",
    "bugs_and_variants/c-unset_var_3": "Vim config backup script",
    "bugs_and_variants/c-unset_var_5": "NVM install downloader",
    "bugs_and_variants/c-unset_var_set_u_1": "OpenStack log collector",
    "bugs_and_variants/c-unset_var_set_u_2": "Audio watermark build",
    "bugs_and_variants/m1-redir_to_func-redir_to_func": "Log redirection helper",
    "bugs_and_variants/m2-loop_once": "Two-file cleanup",
    "bugs_and_variants/m2-loop_once-loop_once": "Directory chmod loop",
    "bugs_and_variants/m2-unset_var": "Backup rsync script",
    "bugs_and_variants/sf-access_after_mv": "ActualBudget updater",
    "bugs_and_variants/sf-access_del_resource": "TV transcode move loop",
    "bugs_and_variants/sf-cd_into_file": "Repo archiver",
    "bugs_and_variants/sf-overwrite_file_2": "Contest winner mover",
    "bugs_and_variants/sf-overwrite_file_3": "Hive DROP log monitor",
    "bugs_and_variants/sf-overwrite_file_4": "SAS script generator",
    "bugs_and_variants/wf-accident": "Accidental recursive delete",
    "bugs_and_variants/wf-capturing_empty_output": "Domain folder creator",
    "bugs_and_variants/wf-claude2": "Claude home wipe",
    "bugs_and_variants/wf-claude3": "Claude temp-file cleanup",
    "bugs_and_variants/wf-claude4": "Claude null-output redirect",
    "bugs_and_variants/wf-claude5": "Claude build cleanup",
    "bugs_and_variants/wf-claude6": "Claude Next.js cleanup",
    "bugs_and_variants/wf-claude_wipe": "Claude cleanup output",
    "bugs_and_variants/wf-confused_mkdir": "Confused mkdir output",
    "bugs_and_variants/wf-delete_home_user": "Deleted home directory",
    "bugs_and_variants/wf-delete_slash": "No-preserve-root delete",
    "bugs_and_variants/wf-empty_path": "Unset PATH startup",
    "bugs_and_variants/wf-find_rm": "Directory clear accident",
    "bugs_and_variants/wf-for_mv": "Archive extract move",
    "bugs_and_variants/wf-move_home": "Move home directory",
    "bugs_and_variants/wf-posix2": "Glob test mismatch",
    "bugs_and_variants/wf-replacement": "Broken file replacement",
    "bugs_and_variants/wf-sc_author": "ShellCheck author example",
    "bugs_and_variants/wf-silly_q": "Broken multi-file rename",
    "bugs_and_variants/wf-troll": r"Obfuscated \sh{rm /}",
    "bugs_and_variants/wf-unexpected_stdin": "System restoration script",
    "bugs_and_variants/wf-unset_var":  "File check",
    "bugs_and_variants/wf-unset_var-cmd_always_fails": "Filesystem preparation",
    "bugs_and_variants/wf-wrong_mkdir": "mkdir output as path",
    "bugs_and_variants/wf-wrong_mv": "Case-only bulk rename",
    "bugs_and_variants/wf-xargs_accident_rm": "Media file conversion",
    "bugs_and_variants/wf-xargs_del_files": "Backup wipe",
}

WILD_BENCHMARK_DESCRIPTIONS = {
    "AFFiNE": r"Dead status handling blocks version updates.",
    "Base Node": r"Unset-var guards abort node startup setup.",
    "BashReduce": r"Unquoted input broadens \sh{rm} target.",
    "Batocera Linux": r"Wrong redirection overwrites log file.",
    "Caker": r"Misquoted \sh{sudo rm -rf} can remove wrong paths.",
    "Cosmos Omnibus": r"Unquoted cleanup path broadens \sh{rm} target.",
    "Crawl4AI": r"Status check is dead under \sh{set -e}.",
    "Danghuangshang": r"Missing-command assumptions break later setup logic.",
    "Dotfiles": r"Shell option misuse and inverted tool checks break setup and uninstall flows.",
    "Edeliver": r"Broken multi-host validation blocks deployment startup.",
    "Embree": r"Unquoted paths broaden build-script arguments.",
    "FaceDetection-DSFD": r"Optional download paths break dataset setup.",
    "Ghorg": r"Dead failure checks hide CI cloning errors.",
    "Gloo Gateway": r"Typo makes uninstaller cleanup dead code.",
    "Hasor": r"Unquoted directory changes break setup on whitespace paths.",
    "IPinfo CLI": r"Unquoted self-path and root variables broaden cleanup targets.",
    "Moby": r"Unset-var flag handling breaks test selection.",
    "La Capitaine Icon Theme": r"Mistyped variables break icon-theme maintenance.",
    "Next.js": r"Unset-var fallback is dead under \sh{set -u}.",
    "Netdata": r"Broken dry-run error handling hides helper failures.",
    "Openpilot": r"Unset-var setup checks abort environment setup.",
    "OpenSC": r"Missing option values can trap argument parsing in a loop.",
    "P4 Compiler": r"Unquoted cleanup paths broaden removal targets.",
    "PgBouncer": r"Edge-case argument handling traps SSL tests in a loop.",
    "PlantsVsZombies": r"Mistyped variables break binding generation.",
    "PyTorch": r"Unset-var fallback aborts environment setup.",
    "RapidPro Docker": r"Unquoted \sh{pwd} broadens cleanup target.",
    "SourcererCC": r"Whitespace in paths breaks shell-script orchestration.",
    "Serverless": r"Empty capture breaks installer branch selection.",
    "SteamTools": r"Unquoted paths broaden maintenance-script arguments.",
    "Swiftenv": r"Unquoted build cleanup broadens removal targets.",
    "Tazpkg": r"Wrong \sh{mktemp} kind makes \sh{cd} fail.",
    "Kubernetes Test Infra": r"Wrong array iteration skips parts of the test matrix.",
    "LibreELEC": r"Dead status checks hide driver update failures.",
    "Multigres": r"Missing arguments break tool-wrapper command construction.",
    "Theme Switcher": r"Inverted tool check blocks theme update.",
    "ToolSave": r"Unquoted uninstall path broadens \sh{rm} target.",
    "V2M": r"Whitespace in path broadens \sh{rm} target.",
    "Ventoy": r"Stale state makes file checks and copy logic misfire.",
    "Whishper": r"Existing destination directories crash installer setup.",
    "vLLM": r"Indirect status checks make failure handling dead.",
    "ch32-data": r"Unquoted paths broaden generator-script arguments.",
    "CS-Notes": r"Undefined variables abort note-build automation.",
}

_WORD_RE = re.compile(r"[a-z0-9]+")
_SHORT_NAME_CACHE = None
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _tokenize(text):
    return _WORD_RE.findall(str(text).lower())


def _fill_to_three(seed, words):
    chars = [c for c in seed if c.isalnum()]
    for c in "".join(words):
        if len(chars) >= 3:
            break
        chars.append(c)
    while len(chars) < 3:
        chars.append("x")
    return "".join(chars[:3])


def _candidate_roots(words):
    if not words:
        return ["bmk"]

    w1 = words[0]
    w2 = words[1] if len(words) > 1 else ""
    w3 = words[2] if len(words) > 2 else ""

    candidates = [
        _fill_to_three((w1[:1] + w2[:1] + w3[:1]), words),  # initials
        _fill_to_three(w1[:3], words),                       # first word
        _fill_to_three((w1[:2] + w2[:1]), words),
        _fill_to_three((w1[:1] + w2[:2]), words),
    ]
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(candidates))


def _build_short_name_cache():
    handles = {}
    used = set()

    for key in sorted(BENCHMARK_DESCRIPTIONS):
        words = _tokenize(BENCHMARK_DESCRIPTIONS[key])
        roots = _candidate_roots(words)

        handle = None
        for root in roots:
            if root not in used:
                handle = root
                break

        if handle is None:
            root = roots[0]
            for suffix in _BASE36:
                candidate = f"{root}{suffix}"  # 4 chars
                if candidate not in used:
                    handle = candidate
                    break

        if handle is None:
            # Extremely unlikely fallback.
            handle = f"{roots[0]}z"

        used.add(handle)
        handles[key] = handle

    return handles


def benchmark_key(path):
    p = Path(str(path))
    parts = p.parts
    if "benchmarks" in parts:
        idx = parts.index("benchmarks")
        key_parts = parts[idx + 1 : -1]
    else:
        key_parts = parts[:-1]
    return "/".join(key_parts)


def benchmark_display_name(path, default=None):
    key = benchmark_key(path)
    if default is None:
        default = key
    return BENCHMARK_DESCRIPTIONS.get(key, default)


def short_name(path, default=None):
    """
    Return a short, deterministic, unique 3-4 character handle.
    Uses display-name acronyms; appends one base36 char only on collisions.
    """
    global _SHORT_NAME_CACHE

    key = benchmark_key(path)
    if _SHORT_NAME_CACHE is None:
        _SHORT_NAME_CACHE = _build_short_name_cache()

    if key in _SHORT_NAME_CACHE:
        return _SHORT_NAME_CACHE[key]

    # Unknown benchmark keys: stable acronym fallback from the key tail.
    parts = [p for p in str(key).split("/") if p]
    tail_words = _tokenize(parts[-1] if parts else key)
    fallback = _candidate_roots(tail_words)[0]
    if default is not None:
        return default
    return fallback
