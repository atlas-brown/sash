#!/bin/sh
if [ ! -f ~/project/files ]; then
    mkdir -p ~/project/files
    cd ~/project/files || exit 1
    rm -rf *
fi
