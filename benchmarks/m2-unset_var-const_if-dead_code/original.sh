!/bin/bash

backuptodirectory=/Volumes/Backup/date/
directorytocopy=/Users/myname/Library/Application Support

if [ ! -d "$directorytocopy" ]; then
    echo "Source path: $directorytocopy doesn't exist"
    exit 1
fi
mkdir -p "$backuptodirectory"
echo copying $directorytocopy
rsync -progress $directorytocopy $backuptodirectory
