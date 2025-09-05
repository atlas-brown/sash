#!/bin/sh
StructuraFoldere=$1
shift
VechimeFis=$2
shift
Dirmutat=$3
shift
cout=1
echo

while [ $# -gt 0 ]
do
if [ -g $# ]
then -ls $#
fi
shift
echo find /"$#" -maxdepth 1 -mtime +"$VechimeFis" -type f -exec mv "{}" "$Dirmutat" \; 
shift
cout=$[cout+1]
shift
done

