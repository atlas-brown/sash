#!/bin/bash

function encode {
echo $1
input=$(<$2)
uppercasemod=`echo $input | tr '[:lower:]' '[:upper:]'`
echo $uppercasemod > $2

for (( i=0; i<${#uppercasemod}; i++ ));
do
    char=`echo "${uppercasemod:$i:1}"`
    if [[ $char == 'A' ]]
    then
        sed -i 's/A/.-\t/' $2
    elif [[ $char == 'B' ]]
    then
        sed -i 's/B/-...\t/' $2
    elif [[ $char == 'C' ]]
    then
        sed -i 's/C/-.-.\t/' $2
    elif [[ $char == 'D' ]]
    then
        sed -i 's/D/-..\t/' $2
    elif [[ $char == 'E' ]]
    then
        sed -i 's/E/.\t/' $2
    elif [[ $char == 'F' ]]
    then
        sed -i 's/F/..-.\t/' $2
    elif [[ $char == 'G' ]]
    then
        sed -i 's/G/--.\t/' $2
    elif [[ $char == 'H' ]]
    then
        sed -i 's/H/....\t/' $2
    elif [[ $char == 'I' ]]
    then
        sed -i 's/I/..\t/' $2
    elif [[ $char == 'J' ]]
    then
        sed -i 's/J/.---\t/' $2
    elif [[ $char == 'K' ]]
    then
        sed -i 's/K/-.-\t/' $2
    elif [[ $char == 'L' ]]
    then
        sed -i 's/L/.-..\t/' $2
    elif [[ $char == 'M' ]]
    then
        sed -i 's/M/--\t/' $2
    elif [[ $char == 'N' ]]
    then
        sed -i 's/N/-.\t/' $2
    elif [[ $char == 'O' ]]
    then
        sed -i 's/O/---\t/' $2
    elif [[ $char == 'P' ]]
    then
        sed -i 's/P/.--.\t/' $2
    elif [[ $char == 'Q' ]]
    then
        sed -i 's/Q/--.-\t/' $2
    elif [[ $char == 'R' ]]
    then
        sed -i 's/R/.-.\t/' $2
    elif [[ $char == 'S' ]]
    then
        sed -i 's/S/...\t/' $2
    elif [[ $char == 'T' ]]
    then
        sed -i 's/T/-\t/' $2
    elif [[ $char == 'U' ]]
    then
        sed -i 's/U/..-\t/' $2
    elif [[ $char == 'V' ]]
    then
        sed -i 's/V/...-\t/' $2
    elif [[ $char == 'W' ]]
    then
        sed -i 's/W/.--\t/' $2
    elif [[ $char == 'X' ]]
    then
        sed -i 's/X/-..-\t/' $2
    elif [[ $char == 'Y' ]]
    then
        sed -i 's/Y/-.--\t/' $2
    elif [[ $char == 'Z' ]]
    then
        sed -i 's/Z/--..\t/' $2
    elif [[ $char == 1 ]]
    then
        sed -i 's/1/.----\t/' $2
    elif [[ $char == 2 ]]
    then
        sed -i 's/2/..---\t/' $2
    elif [[ $char == 3 ]]
    then
        sed -i 's/3/...--\t/' $2
    elif [[ $char == 4 ]]
    then
        sed -i 's/4/....-\t/' $2
    elif [[ $char == 5 ]]
    then
        sed -i 's/5/.....\t/' $2
    elif [[ $char == 6 ]]
    then
        sed -i 's/6/-....\t/' $2
    elif [[ $char == 7 ]]
    then
        sed -i 's/7/--...\t/' $2
    elif [[ $char == 8 ]]
    then
        sed -i 's/8/---..\t/' $2
    elif [[ $char == 9 ]]
    then
        sed -i 's/9/----.\t/' $2
    elif [[ $char == 0 ]]
    then
        sed -i 's/0/-----\t/' $2

    fi
done
counter=`grep -P '\t' *.txt | wc -w`
for (( i=0; i<counter; i++ ));
do
    sed -i 's/ //' *.txt
done
}

