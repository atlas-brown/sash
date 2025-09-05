#!/bin/sh

ve="1013"

if  [ "$ve" == "1013" ]; then
    echo "match"
    /bin/launchctl load -wF /Library/LaunchDaemons/com.skull.tst.plist
else
    exit
fi

