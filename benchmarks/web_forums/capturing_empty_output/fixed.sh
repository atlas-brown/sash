#!/bin/sh
printf " Which Name needs to create? (y/n): "; read dom


     if [ "$dom" = "y" ]; then
    printf " Type an Domain name: " ; read domTemp

path=/home/rakesh/$domTemp

a=`mkdir -v -p -- "$path"`

echo "$a"

fi
