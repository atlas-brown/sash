#!/bin/bash -x

source credentials.sh
OPTARG=""
while getopts :i:x:n name
do
    case $name in
        x)  inputfile="$OPTARG" ;;
        i)  outputPath="$OPTARGS" ;;
        n)  dirName="$OPTARG" ;;
    esac
done

if [ ! "$dirName" ]
then
    mkdir $dirName || echo "error while creating dir"
fi
while read -r line;
do
    touch "$line"
    mv  "$line" "$dirName"
done < $inputfile

