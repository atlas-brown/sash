#!/bin/sh
# https://superuser.com/questions/307057/help-i-ran-find-mtime-1-exec-rm-by-accident
find / -mtime +1 -exec rm {} \;
