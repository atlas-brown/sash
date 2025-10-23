#!/bin/sh

rm -vrf /Users/krzysztofpaszta/CSVtoGD2/*
rm -vrf /Users/krzysztofpaszta/CSVtemporary2/*
cd /Users/krzysztofpaszta/temporaryprojects
for repo in $(cat /users/krzysztofpaszta/repolinks.csv); do
    git clone "$repo"
    dir=${repo##*/}
    find /users/krzysztofpaszta/temporaryprojects/"$dir" -name "*.fnt" -o -name "*.png" -o -name "*.ttf" -o -name "*.asset" -o -name "*.jpeg" -o -name "*.tga" -o -name "*.tif" -o -name "*.bmp" -o -name "*.jpg" -o -name "*.fbx" -o -name "*.prefab" -o -name "*.flare" -o -name "*.ogg" -o -name "*.wav" -o -name "*.anim" -o -name "*.mp3" -o -name "*.tiff" -o -name "*.otf" -o -name "*.hdr" >> /users/krzysztofpaszta/CSVtemporary2/ASSETS-LIST-"$dir".csv
    while read in ; do
      cut -d'/' -f6- >> /users/krzysztofpaszta/CSVtoGD2/"$dir".csv # bug here: cut will consume all input which is meant for the while loop
    done < /users/krzysztofpaszta/CSVtemporary2/ASSETS-LIST-"$dir".csv
done
#rm -vrf /Users/krzysztofpaszta/temporaryprojects/*
#echo Repo deleted
