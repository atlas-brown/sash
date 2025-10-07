#!/bin/sh

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR # bug here (1): should be $DIR/*
do
    cp $f $f.bak
   sed 's+$OLD+$NEW+g' $f.bak > $f
   [ -f "$f" ] # bug here (2): useless conditional
   rm -f $f.bak
done
