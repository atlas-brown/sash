# Documentation of the high profile benchmarks

In `sosp_benchmarks/benchmarks/highprofile`

TODO later: add fixed variants for all originals
can also add other variants to highlight other interesting aspects of sash vs rw
can center the whole evaluation around these highprofile scripts and variants


## `original/steam.sh`

The steam bug; what could happen:
in `steam_reset`, if `$0` is unexpected shape (plain filename) then `$STEAMROOT` can be empty, and the line
`rm -fr $STEAMROOT/*` can end up expanding to `rm -fr /*`



## `original/backup_methods.sh`

Lines 185--191:

        $command 2> $logfile | $nice $compress_bin -f -q -9 > $file_to_create.$ext 2> $logfile
        file_to_create="$file_to_create.$ext"
    fi
    
    if [[ $? -gt 0 ]]; then
        warning "Unable to exec \$command; check \$logfile"
        rm -f $file_to_create

Bug is that $? is always 0 due to the assignment, and so the backup always fails and the file is deleted

References:
<https://web.archive.org/web/20190920103757/https://dragula.viettug.org/blogs/675.html>
<https://github.com/icy/bash-coding-style/blob/master/LESSONS.md#2012-backup-manager-kills-a-french-company>



## `original/bumblebee_install.sh`

Line 361

    rm -rf /usr /lib/nvidia-current/xorg/xorg



## `original/n.sh`

    # Line 16
    N_PREFIX=${N_PREFIX-/usr/local}
    # ...
    # line 152
        for d in bin lib share include; do
          rm -rf $N_PREFIX/$d

What's the problem? Everything in `/usr/local/bin` etc is wiped

Another bug: I suppose `$N_PREFIX` could be set to something with a space in it, and then the script would wipe the whole directories as well

Reference: <https://github.com/tj/n/issues/86>



## `original/itunes.sh`

I just added this myself <span class="timestamp-wrapper"><span class="timestamp">[2025-07-18 Fri]</span></span>, it's the minimized version of the buggy itunes installer script line



## `original/squid.init.sh`

Reference <https://github.com/icy/bash-coding-style/blob/master/LESSONS.md#2015-restarting-squid-31-on-a-rhel-system-removes-all-system-files>

This script doesn't have a bug, but the reference above speculates that the original (buggy) version of the script didn't have a constant definition of `$SQUID_PIDFILE_DIR`, and under some circumstances could be empty
Hence I think we can keep this one but mark it as correct, and then have a variant that omits the constant def



## `variants/itunes.sh`

Snippet from the FIXED itunes updater script



## TODO `variants/n.sh`

A minimized variant of original n.sh, but it doesn't fix the bug?
Where did this come fmrom?



## `variants/org.sh`

Minimized steam bug, see above - not in the function def



## `variants/redhat.sh`

This is a minimized version of the squid script above, modified to check if stop succeeded?
Since the const definition of `$SQUID_PIDFILE_DIR` is removed, this could now be dangerous



## `variants/redhat.sh`

Variant of squid script that omits constant def of the problematic variable.



## `variants/{safefix,subtle,unsafefix}.sh`

Variants from the HotOS paper

