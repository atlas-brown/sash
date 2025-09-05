#!/bin/sh

x=1
while [ $x -le 3 ]
do
  cd $PWD/$x
  echo Changed to dir: $PWD
  count=ls | wc -l
  echo $count
  if [[ "$count" -eq 1 ]]
  then
    echo $PWD has 1 file/folder
  fi
  echo --------------------------------------------------
  cd ..
  x=$(( $x + 1 ))
done

