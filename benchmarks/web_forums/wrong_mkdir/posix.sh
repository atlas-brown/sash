#!/bin/sh

# https://community.unix.com/t/move-file-in-current-date-folder-through-shell-script/384145

file=baktestuser.txt
for user in $(cat $file);
do
cd /usr/local/ddos
root_folder=$(mkdir "$(date +"%d-%m-%Y")")
cp baktestuser.txt $root_folder
done
