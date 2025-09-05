#!/usr/bin/env bash
for file in "/home/user/*"
do
   tr '[:lower:]' '[:upper:]' < "$file" > "$file"
done

