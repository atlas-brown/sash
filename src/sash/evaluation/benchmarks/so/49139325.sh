#!/bin/sh
echo "directory name is " $1
if [ ! -d $1 ];  
then
  echo "ERROR: directory doesn't exist"
fi

