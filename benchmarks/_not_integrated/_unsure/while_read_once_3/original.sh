#!/bin/bash
# https://stackoverflow.com/questions/70460657/where-is-the-bug-in-this-one-line-bash-script
find . -name '*.mp4' -printf '%P\n' || true |
    while read -r FILE; do
        ffmpeg -loglevel error -y -i "$FILE" -ss 00:00:01.000 -vframes 1 junk.png;
    done

# ... shellcheck warns...
