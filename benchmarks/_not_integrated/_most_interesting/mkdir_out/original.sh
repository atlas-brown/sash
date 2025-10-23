#!/bin/bash
#https://stackoverflow.com/questions/73501167/issue-in-mkdir-output-to-variable
echo -n " Which Name needs to create? (y/n): "; read dom


     if [ "$dom" == "y" ]; then
    echo -n " Type an Domain name: " ; read domTemp

path=/home/rakesh/$domTemp

a=`mkdir -p -- "$path"` # bug here: mkdir does not produce output

echo "$a"

fi

