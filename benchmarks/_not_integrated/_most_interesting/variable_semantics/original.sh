#!/bin/bash

FPath=$(grep $1 $HOME/.restore.info | cut -d":" -f2)
FName=$(grep $1 $HOME/.restore.info | cut -d":" -f1)
if [ $# -eq 0 ]
then
        echo "No input detected"
        exit $?

elif [ "$FName" = $1 ]
then
        echo " Match found and restored to its original location"
        mv ~/deleted/$1 $FPath
else
        echo "File does not exist"
        exit $?
fi

# Argument semantics depend on word splitting
# Argument roles/types are different depending on position and might change based on word splitting

