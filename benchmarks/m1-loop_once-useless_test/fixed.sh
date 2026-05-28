#!/bin/sh

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR/*
do
    cp $f $f.bak
   sed 's+$OLD+$NEW+g' $f.bak &gt; $f
   [ -f "$f" ] && rm -f $f.bak
done
