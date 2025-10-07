#!/bin/bash

# https://stackoverflow.com/questions/48706718/error-with-bash-script-using-rsync-to-copy-directory-with-space-in-directory-nam

backuptodirectory=/Volumes/Backup/date/
directorytocopy=/Users/myname/Library/Application Support # bug here (1): "Support" will be interpreted as a command name

if [ ! -d "$directorytocopy" ]; then # bug here (2): directorycopy is unset (check will always succeed)
    echo "Source path: $directorytocopy doesn't exist"
    exit 1
fi
mkdir -p "$backuptodirectory" # bug here (3): due to bug (2), dead code
echo copying $directorytocopy
rsync -progress $directorytocopy $backuptodirectory
