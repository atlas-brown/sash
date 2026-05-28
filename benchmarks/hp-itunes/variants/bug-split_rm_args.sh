#!/bin/sh

target="/usr"

# if iTunes application currently exists, delete it
if [ -e $target"Applications/Arc.app" ] ; then
    rm -rf $target "Applications/iTunes.app" 2> /dev/null # bug here: $2 can word-split
fi
