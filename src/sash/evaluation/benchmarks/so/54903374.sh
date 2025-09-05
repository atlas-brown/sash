#!/bin/sh
# Create the structure of folders that will contain the result files
export perl_git_dir=path1
export OUTPUT_DIR=path2
mkdir $OUTPUT_DIR/output_perl

for FILE in `ls *.sh`
 do
    echo  "file is:"$FILE
    if [ -f "$FILE" ];then
        name=${FILE%.*}
        mkdir -p $OUTPUT_DIR/output_perl/"$name"
    fi;     
done

for entry in `ls *.sh`
 do
    if [ -f "$entry" ];then
        echo "enty is "$entry 
        echo "$entry" >> stdout.txt
        echo "$entry" >> stderr.txt
        ./$entry >> stdout.txt 2>> stderr.txt
    fi;     
done

