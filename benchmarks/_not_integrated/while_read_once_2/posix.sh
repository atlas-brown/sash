#!/bin/sh

FLAGS_IN=MY_TEXT_FILE_CONTAINING_LOTS_OF_LINE

while read  BENCHMARK DATASET  CF
do
    echo "$BENCHMARK"
    echo "$DATASET"
    echo "$CF"
    N=$((N + 1))

cd $tmp
echo "**********************************************************"
            GCC_OPT="-O3"
            OPT_FLAGS=$CF
###### do sth
# unfortunately not the entire script is shown but the poster confirms that this was the problem


tmp=$PWD
done <  $FLAGS_IN

exit 0
