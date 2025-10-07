#!/bin/bash

# https://stackoverflow.com/questions/48568740/read-file-line-by-line-and-delete-after

unset n
while read -r user work codename; do
  echo $user $work $codename
  : $[n++]
done <connectedclients.now
sed "1 $n d" connectedclients.now # bug here: should be "1,${n}d"
