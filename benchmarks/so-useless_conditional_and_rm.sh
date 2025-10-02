#!/bin/sh

# https://stackoverflow.com/questions/48195715/sh-script-to-replace-text-in-multiple-files

OLD="/net/origin/devdata1/slin" 
NEW="/toolscommon/test/HATS" 
DIR="/home/AutoTest" 
for f in $DIR # bug here (1): should be $DIR/*
do 
    cp $f $f.bak 
   sed 's+$OLD+$NEW+g' $f.bak > $f # bug here (2): single-quoted sed pattern with variables
   [ -f "$f" ] # bug here (3): useless conditional
   rm -f $f.bak 
done
