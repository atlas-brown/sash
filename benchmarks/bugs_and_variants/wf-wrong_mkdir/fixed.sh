#!/bin/sh

mkdir -p /usr/local/ddos
file=baktestuser.txt
for user in $(cat $file);
do
cd /usr/local/ddos
root_folder=$(date +"%d-%m-%Y")
mkdir -p $root_folder
cp baktestuser.txt $root_folder
done
