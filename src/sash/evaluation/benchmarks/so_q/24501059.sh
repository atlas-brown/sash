#!/bin/bash
set -e

increment=9
file1="path/to/file1"
file2="path/to/file2"
file3="path/to/file3"

# End index of header in first file
file1_start=2138
midpoint=$(( $file1_start + 1 ))

file1_wc=($(wc $file1))
file2_wc=($(wc $file2))
file3_wc=($(wc $file3))

# Get a line count for the three different flat text files, as an upper bound index
ceil1=${file1_wc[0]}
ceil2=${file2_wc[0]}
ceil3=${file3_wc[0]}

# Initialize end point indices
line="$(head -$midpoint $file1 | tail -1 | awk '{print $1;}')"
line2=$(grep -n -e "$line" $file2 | cut -f1 -d:)
line3=$(grep -n -e "$line" $file3 | cut -f1 -d:)

# Initialize starting point indices
last1=$midpoint
last2=$line2
last3=$line3

# Update "midpoint" index
midpoint=$(($midpoint+$ceil1/$increment))

while [ $midpoint -lt $ceil1 ]
do
    line="$(head -$midpoint $file1 | tail -1 | awk '{print $1;}')"
    line2=$(grep -n -e "$line" $file2 | cut -f1 -d:)
    line3=$(grep -n -e "$line" $file3 | cut -f1 -d:)

    # Calculate range of indices for subset number $increment
    span1=$(($midpoint-$last1))

    echo "Line of interest: span2=$(($line2-$last2))"
    # ***NOTE***: The below statement is where it is failing for odd $increment
    span2=$(($line2-$last2))

    span3=$(($line3-$last3))

    # Set index variables for next iteration of file traversal
    index=$(($index+1))
    last1=$midpoint
    last2=$line2
    last3=$line3

    # Increment midpoint index variable
    midpoint=$(($midpoint+$ceil1/$increment))
done
