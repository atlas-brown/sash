#!/bin/sh
mkdir -p ../update_error_handled || exit 1
find ./* -newermt $(date +%Y-%m-%d -d '7 day ago') -type f -print | xargs -I '{}' mv {} ../update_error_handled
