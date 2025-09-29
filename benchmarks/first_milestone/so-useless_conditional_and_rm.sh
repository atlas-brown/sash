#!/bin/sh

# https://stackoverflow.com/questions/48195715/sh-script-to-replace-text-in-multiple-files

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR # check: loop only iterates once
do
    cp $f $f.bak
   sed 's+$OLD+$NEW+g' $f.bak > $f
   [ -f "$f" ] # check: useless test
   rm -f $f.bak
done
