from pathlib import Path


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
    "commits/unset_func": "Git version check",
    "commits/unset_var_2": "VSCode patch helper",
    "commits/unset_var_3": "Vim config backup script",
    "commits/unset_var_5": "NVM install downloader",
    "commits/unset_var_set_u_1": "OpenStack log collector",
    "commits/unset_var_set_u_2": "Audio watermark build script",
    "milestone_1/redir_to_func-redir_to_func": "Log redirection helper",
    "milestone_2/loop_once": "Two-file cleanup",
    "milestone_2/loop_once-loop_once": "Directory chmod loop",
    "milestone_2/unset_var-const_if-dead_code": "Rsync directory copy script",
    "simple_fs/access_after_mv": "ActualBudget update helper",
    "simple_fs/access_del_resource": "TV transcode move loop",
    "simple_fs/cd_into_file": "Repo archiver",
    "simple_fs/overwrite_file_2": "Contest winner mover",
    "simple_fs/overwrite_file_3": "Hive DROP log monitor",
    "simple_fs/overwrite_file_4": "SAS script generator",
    "web_forums/capturing_empty_output": "Domain folder creator",
    "web_forums/unexpected_stdin": "System restoration script",
    "web_forums/unset_var":  "File check",
    "web_forums/unset_var-cmd_always_fails": "Filesystem preparation helper",
}


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
