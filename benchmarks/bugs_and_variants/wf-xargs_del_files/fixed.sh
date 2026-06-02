#!/bin/sh
mkdir -p ../update_error_handled
find ./* -newermt $(date +%Y-%m-%d -d '7 day ago') -type f -print | xargs -I files mv files ../update_error_handled
