#!/bin/sh

echo "-------------------------------------------------"
echo "Arguments:"
echo "Old File String: $1"
echo "New File Name Head: $2"
echo "Directory to Change: $3"
echo "-------------------------------------------------"
oldname="$1"
newname="$2"
abspath="$3"
echo "Updating all files in '$abspath' to $newname.{extension}"

for file in $(ls $abspath);
do
    echo $file
    echo $file | sed -e "s/$oldname/$newname/g"
    newfilename=$("echo $file| sed -e \"s/$oldname/$newname/g\"")
    echo "NEW FILE: $newfilename"
    mv $abspath/$file $abspath/$newfilename
done

