#!/bin/sh
STEAMROOT="$(cd "${0%/*}" && echo $PWD)"
 # ... more lines ...
 rm -rf $STEAMROOT/*
