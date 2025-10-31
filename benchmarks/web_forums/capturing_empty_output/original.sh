#!/bin/bash
echo -n " Which Name needs to create? (y/n): "; read dom


     if [ "$dom" == "y" ]; then
    echo -n " Type an Domain name: " ; read domTemp

path=/home/rakesh/$domTemp

a=`mkdir -p -- "$path"`

echo "$a"

fi

