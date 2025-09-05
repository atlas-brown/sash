#!/bin/sh
STEAMROOT="$(cd "${0%/*}" && echo $PWD)"
if [ "$STEAMROOT" != "" ]; then
rm -fr $STEAMROOT/*
else
echo "Bad script path: $0"; exit 1
fi
