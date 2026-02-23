# https://superuser.com/questions/998089/mv-command-lost-all-files-can-find-files-by-locate-function-but-not-in-file-man
# FIXED: find /home/billy/Downloads -type f -iname "*.mkv" -o -iname "*.mp4" -o -iname "*.avi" | xargs /bin/rm -f | xargs -I list mv list /home/billy/Videos/
