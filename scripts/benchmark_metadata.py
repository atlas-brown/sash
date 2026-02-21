from pathlib import Path
import re


BENCHMARK_NAMES = {
    "high_profile/c00-steam": "Steam updater",
    "high_profile/c01-bumblebee": "NVIDIA driver installer",
    "high_profile/w00-itunes": "iTunes updater",
    "high_profile/w01-squid": "Squid init script",
    "high_profile/c02-n": "Node.js version manager",
    "high_profile/c03-backup_manager": "Ubuntu backup manager",
    "milestone_1/const_loop": "DigitalOcean snapshot",
    "milestone_1/loop_once-useless_test": "AutoTest config rename",
    "milestone_1/unset_var_1": "OhMyZsh update script",
    "milestone_2/rm_root": "MongoDB backup script",
    "web_forums/rm_root_2": "AIX server data gather",
    "commits/debootstrap": "Debian debootstrap",
    "simple_fs/overwrite_file": "SLURM cluster overwrite",

    # TODO: Please review wording.
    "commits/const_cond": "Git config check",
    "commits/debootstrap_2": "Debian debootstrap 2",
    "commits/ignored_command_v": "Zsh installer check",
    "commits/makefile": "Camlp5 install wrapper",
    "commits/cp_nonexistent": "TIB deploy script",
    "commits/unset_func": "Git version check",
    "commits/unset_var_2": "VSCode patch helper",
    "commits/unset_var_3": "Vim config backup script",
    "commits/unset_var_5": "NVM install downloader",
    "commits/unset_var_set_u_1": "OpenStack log collector",
    "commits/unset_var_set_u_2": "Audio watermark build",
    "milestone_1/redir_to_func-redir_to_func": "Log redirection helper",
    "milestone_2/loop_once": "Two-file cleanup",
    "milestone_2/loop_once-loop_once": "Directory chmod loop",
    "milestone_2/unset_var-const_if-dead_code": "Backup rsync script",
    "simple_fs/access_after_mv": "ActualBudget updater",
    "simple_fs/access_del_resource": "TV transcode move loop",
    "simple_fs/cd_into_file": "Repo archiver",
    "simple_fs/overwrite_file_2": "Contest winner mover",
    "simple_fs/overwrite_file_3": "Hive DROP log monitor",
    "simple_fs/overwrite_file_4": "SAS script generator",
    "web_forums/capturing_empty_output": "Domain folder creator",
    "web_forums/claude_wipe": "Claude cleanup output",
    "web_forums/unexpected_stdin": "System restoration script",
    "web_forums/unset_var":  "File check",
    "web_forums/unset_var-cmd_always_fails": "Filesystem preparation",
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

    for key in sorted(BENCHMARK_NAMES):
        words = _tokenize(BENCHMARK_NAMES[key])
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
    return BENCHMARK_NAMES.get(key, default)


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
