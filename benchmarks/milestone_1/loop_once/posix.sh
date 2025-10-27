#!/bin/sh

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR # bug here: should be $DIR/* (loops once)
do
    cp $f $f.bak
   sed 's+$OLD+$NEW+g' $f.bak &gt; $f
   [ -f "$f" ]
   rm -f $f.bak
done
