#!/bin/sh
# https://www.linuxquestions.org/questions/linux-general-1/mv-is-not-working-right-782971/

for file in *.JPG; do
    mv "$file" "$(sed 's/\.JPG//' $file)".jpg;
done
