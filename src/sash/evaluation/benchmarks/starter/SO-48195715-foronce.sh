#!/bin/bash

# https://stackoverflow.com/questions/48195715/sh-script-to-replace-text-in-multiple-files
# See comments below for ShellCheck info

# There's a bug here not mentioned in the question: loop will only loop once,
# because $DIR does not end with /* (which would expand and create a list to iterate over)

# Warning about looping only once in all possible executions sounds reasonable to me

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR; do # bug 1: should have been $DIR/* (see above) (ShellCheck does not detect this (in this case))
  cp $f $f.bak
  sed 's+$OLD+$NEW+g' $f.bak > $f # bug 1: single-quoted sed pattern (ShellCheck detects this)
  [ -f "$f" ] # bug 2: missing '&&' between 'test' and 'rm' (heuristic: useless code, ShellCheck doesn't detect this)
  rm -f $f.bak
done
