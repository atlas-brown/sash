#!/bin/sh

# https://stackoverflow.com/questions/49562688/trying-to-iterate-through-files-stored-in-variables

file1="./file1"
file2="./file2"
text="searched text"
for i in $file1,$file2; do # bug here: loop only runs once
sed -i.txt '/$text/d' $i
done
