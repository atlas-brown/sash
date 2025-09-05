#!/bin/sh
# if iTunes application currently exists, delete it
if [ -e $2Applications/iTunes.app ] ; then
    rm -rf $2Applications/iTunes.app 2> /dev/null
fi
