#!/bin/sh
cat $d | grep send | awk '{print $1}' | awk '$1 > 40.0 {print $0;}' > /dev/null 2>&1

if [ "$?" = "0" ]
    then
    host=$(cat $d | grep Host | awk '{print $1}')
    echo "$host has high sending usage!"
else
    echo "--" > /dev/null 2>&1
fi

