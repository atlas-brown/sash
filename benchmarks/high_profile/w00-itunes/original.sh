#!/bin/sh

# if iTunes application currently exists, delete it
if [ -e $2Applications/Arc.app ] ; then
    #rm -rf $2Applications/iTunes.app 2> /dev/null # bug here
    echo $2Applications/Arc.app
fi
