#! /usr/bin/env sh

# Q: How can you fix the script to prevent the warnings?

src="$1"
dst="$2"

if [ -z "$src" ] && [ ! -d "$dst" ]; then
    echo "usage: $0 src dst"
    exit 1
fi

rm -rf "$dst"/*      # Clear out dst
cp "$src"/* "$dst"/  # Copy all files from src
rm -rf "$src"/*      # Clear out src
