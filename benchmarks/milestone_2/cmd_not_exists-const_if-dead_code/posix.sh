#!/bin/sh

backuptodirectory=/Volumes/Backup/date/
directorytocopy=/Users/myname/Library/Application Support # bug here (1): "Support" will be interpreted as a command name

if [ ! -d "$directorytocopy" ]; then # bug here (2, 3): directorycopy is unset, check will always succeed
    echo "Source path: $directorytocopy doesn't exist"
    exit 1
fi
mkdir -p "$backuptodirectory" # bug here (4): dead code due to bug (3)
echo copying $directorytocopy
rsync -progress $directorytocopy $backuptodirectory
