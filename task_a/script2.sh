#! /usr/bin/env sh

# Q: Assuming /usr should not be deleted, is this script buggy? If so, why do you think SaSh does not warn about it?

dir="/usr"
echo "$dir" > file
dir="$(cat file)"
rm -rf "$dir"
