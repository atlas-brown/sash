#!/bin/bash

# https://unix.stackexchange.com/questions/560038/while-loop-deletes-all-files-and-becomes-stuck-in-loop
# ShellCheck does not detect this

touch while/151234
touch while/152355
touch while/151694
touch while/153699
touch while/156946
NUMSNAPS=$(ls while | awk '{print $1}' | wc -l)
RETAIN=2

while [ "$RETAIN" -le "$NUMSNAPS" ]; do # bug here: RETAIN is not recalculated so the condition is constant
  OLDEST=$(ls | awk '{print $1}' | head -n 1)
  rm "$OLDEST"
done
