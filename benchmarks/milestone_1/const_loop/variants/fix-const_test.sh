#!/bin/sh

touch while/151234
touch while/152355
touch while/151694
touch while/153699
touch while/156946
NUMSNAPS=$(ls while | awk '{print $1}' | wc -l)
RETAIN=2

while [ "$RETAIN" -le "$NUMSNAPS" ] || [ "a" = "b" ]; do # diff: add a const-false test
    OLDEST=$(ls | awk '{print $1}' | head -n 1)
    rm "$OLDEST"
    NUMSNAPS=$(ls while | awk '{print $1}' | wc -l)
done
