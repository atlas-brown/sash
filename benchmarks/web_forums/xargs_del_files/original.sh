#!/bin/sh
# https://stackoverflow.com/questions/64098546/why-find-piped-to-xargs-mv-deleted-my-files
find ./* -newermt $(date +%Y-%m-%d -d '7 day ago') -type f -print | xargs -I '{}' mv {} ../update_error_handled
