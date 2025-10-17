#!/bin/sh -x

. credentials.sh
OPTARG=""
while getopts :i:x:n name # bug here (1): should be i:x:n: (and because of that, dirName is always empty)
do
    case $name in
        x)  inputfile="$OPTARG" ;;
        i)  outputPath="$OPTARGS" ;; # bug here (2): should be $OPTARG
        n)  dirName="$OPTARG" ;;
    esac
done

if [ ! "$dirName" ] # bug here (3): intent was [ ! -d "$dirName" ], so now mkdir always fails if it runs
then
    mkdir $dirName || echo "error while creating dir"
fi
while read -r line;
do
    touch "$line"
    mv  "$line" "$dirName"
done < $inputfile

# there's plenty more bugs in this script, but these are the main ones
