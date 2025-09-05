#!/bin/sh

if [ $# -ne 1 ]; then
    echo "Invalid usage"
    echo "Usage: $0 <logfile>"
    exit 1
fi


if [[ $1 == *"outputfile" ]]; then        
    echo "Found it"
else    
    echo "Search fail"
fi

