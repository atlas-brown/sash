#!/bin/sh

# https://unix.stackexchange.com/questions/231513/bash-move-files-of-specific-pattern

# ARCHIVE_FILEMASK="????-??-??_E_MDT_0_0.7z"
# FIXED: FILEMASK="?????????_?????_A_?_????????????????-?????????"
# FIXED: extractDir=/path/to/dir
# FIXED: dest=/path/to/dest

# FIXED: for f in ${ARCHIVE_FILEMASK}
# FIXED: do
# FIXED:     if 7z e -aoa -o"${extractDir}" "$f";
# FIXED:     then
# FIXED:         mv "${extractDir}/${FILEMASK}".xml "$dest"
# FIXED:     fi
# FIXED: done

