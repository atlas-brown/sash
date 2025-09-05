#!/bin/bash 
FILENAME=/xyz/console.log  

    while :
    do  
        FILESIZE=$(du -h "$FILENAME")

        ####FILESIZE=$(stat -c%s "$FILENAME")

        if [[ $FILESIZE > 10K ]];
        then
            echo "$FILENAME is too large = $FILESIZE"
            echo "$(date ) is here"
            cd "/etc"
            $sudo logrotate -f logrotate.conf
            echo "$ Newer version of log file is created"
        else
            echo "Log limit is not reached"
        fi
    sleep 60s

done

exit 0

