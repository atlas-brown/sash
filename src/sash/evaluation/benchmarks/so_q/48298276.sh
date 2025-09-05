#!/bin/sh
count=$(ls -1 /var/mds_backup/archives | wc -l)
echo "$count"
if [ "$count" -gt "3" ]; then
    difference=`expr $count - 3`
    rm -f $(ls -1t  /var/mds_backup/archives | tail -n $difference)
else
    echo "Nothing to Delete !!!"
fi

