#!/bin/sh

backuptodirectory=/Volumes/Backup/date/

_set_directorytocopy() {
    directorytocopy=
}

if [ ! -d "$directorytocopy" ]; then # bug here (1, 2): directorycopy is unset, check will always succeed
    echo "Source path: $directorytocopy doesn't exist"
    exit 1
fi
mkdir -p "$backuptodirectory" # bug here (3): dead code due to bug (2)
echo copying $directorytocopy
rsync -progress $directorytocopy $backuptodirectory
