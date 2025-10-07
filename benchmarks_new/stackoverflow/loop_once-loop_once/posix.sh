#!/bin/sh

# https://stackoverflow.com/questions/48919816/i-am-having-a-hard-time-why-the-if-statements-dont-work-in-shellscript

DIR='/home/collin2/'
x=1
echo "Please enter directory"
read directory

for directory in "$DIR"; # bug here (1): should be $DIR*
do
        if [ -d  "$directory" ]; 
    then echo "This is a directory Please enter the file name"
            read filename
            while [ $x -le 3 ]; do

            for filename in  "$directory"; # bug here (2): should be unquoted
        do
            if [ -r "$filename" ]
            then echo "The filename is readable" 
                echo "Please Enter a word "
                read word
                grep "$word" "$filename"
                exit 1


            fi

        done
        echo "Doesn't exist please try again"
        read filename 


        x=`expr $x + 1`

            done


     #exit 1

        fi

done
 echo "not a directory"
