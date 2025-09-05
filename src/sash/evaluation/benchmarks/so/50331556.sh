#! /bin/bash

# This script checks if your Apache log is older than two weeks.
# If so, the files will be deleted

# Defining savepath
savePath="/var/log/test.log"

# Startup
printf "\n*** Starting logrotate at $(date +'%m-%d-%y %H:%M:%S') ***" >> $savePath

# Check if Apache logs older than two weeks are existing
apacheCount=`sudo /usr/bin/find /var/log/apache2/ -iname "access.log.*.gz" -mtime +15 | wc -l`

# If so, delete 'em!
if [ "$apacheCount" != "0" ]; then

    sudo /usr/bin/find /var/log/apache2/ -iname "access.log.*.gz" -mtime +8 -exec rm -f {} \;
    newValue=sudo /usr/bin/find /var/log/apache2/ -iname "access.log.*.gz" -mtime +15 | wc -l

    if [ "$newValue" == "0" ]; then
            printf "\n$(date +'%m-%d-%y %H:%M:%S'): $apacheCount Apache Log(s) has / have been deleted." >> $savePath
    else
            printf "\n$(date +'%m-%d-%y %H:%M:%S'): There was an error. $(($apacheCount-$newValue)) items were not deleted." >> $savePath
    fi
else
    printf "\n$(date +'%m-%d-%y %H:%M:%S'): No Apache Log older than two weeks found." >> $savePath
fi

