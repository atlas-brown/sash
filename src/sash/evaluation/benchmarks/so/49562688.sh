#!/bin/sh
file1="./file1"
file2="./file2"
text="searched text"
for i in $file1,$file2; do
sed -i.txt '/$text/d' $i
done

