#!/bin/sh

unset n
while read -r user work codename; do
  echo $user $work $codename
  n=$((n+1))
done <connectedclients.now
sed "1 $n d" connectedclients.now # bug here: should be "1,${n}d"
