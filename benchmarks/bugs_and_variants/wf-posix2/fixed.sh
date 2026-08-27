#!/bin/sh
# https://stackoverflow.com/questions/48854121/check-if-files-of-a-given-type-exist-in-bash-shell
find /Users/myname/Downloads/ -maxdepth 1 -name "*.zip" -print0 | xargs -0 mv -f -t /Users/myname/Downloads/zip/
