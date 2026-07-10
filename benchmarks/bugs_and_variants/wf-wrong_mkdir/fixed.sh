#!/bin/sh

# https://community.unix.com/t/move-file-in-current-date-folder-through-shell-script/384145

# FIXED: file=baktestuser.txt
# FIXED: for user in $(cat $file);
# FIXED: do
# FIXED: cd /usr/local/ddos
# FIXED: root_folder=$(mkdir "$(date +"%d-%m-%Y")")
# FIXED: cp baktestuser.txt $root_folder
# FIXED: done
