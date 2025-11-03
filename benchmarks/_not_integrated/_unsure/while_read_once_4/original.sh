#!/bin/sh
# https://stackoverflow.com/questions/57078283/bash-loop-over-files-mysterious-bug
find . -name "*.mp3" | while read fname ; do
     echo "$fname";
     ls "$fname";
     mplayer "$fname" ;
     echo "$fname" ;
done

# ... shellcheck warns ...
