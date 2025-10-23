#!/bin/bash
# https://askubuntu.com/questions/1356169/bash-scripting-my-script-deletes-a-working-folder-prematurely-how-do-i-fix
cd /storage/sort_tv/
mkdir workingfolder
for i in *.mp4;
  do name=`echo "$i" | cut -d'.' -f1`
  echo "$name"
sudo ffmpeg -i "$i" -map_metadata -1 -c:v copy -c:a copy -map 0:a -map 0:v "workingfolder/${i%.*}.mp4" &&
mv -f workingfolder/* /storage/sort_tv
rm -rf workingfolder # bug here: this line gets executed prematurely
done
