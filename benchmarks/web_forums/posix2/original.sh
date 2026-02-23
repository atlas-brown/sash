#!/bin/sh
# https://stackoverflow.com/questions/48854121/check-if-files-of-a-given-type-exist-in-bash-shell
[ -f "/Users/myname/Downloads/*.zip" ] && mv -f /Users/myname/Downloads/*.zip /Users/myname/Downloads/zip/ || echo 'Nothing'
