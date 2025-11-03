#!/bin/sh

cd /storage/sort_tv/
mkdir workingfolder
for i in *.mp4;
    do name=`echo "$i" | cut -d'.' -f1`
    echo "$name"
    sudo ffmpeg -i "$i" -map_metadata -1 -c:v copy -c:a copy -map 0:a -map 0:v "workingfolder/${i%.*}.mp4" &&
    mv -f workingfolder/* /storage/sort_tv # bug here: attempt to move from directory that was deleted in the previous iteration
    rm -rf workingfolder
done
