#!/usr/bin/env bash
echo 'Hello this is the test of' `date`
echo 'arguments number is ' $#
if [ $# -eq 4 ]
then
    for a in $@
    do
    if [ -d $a ]
    then
        ls $a > /tmp/contenu
        echo "contenu modified"
    elif [ -f $a ]
        then
#        this instruction must set a numeric value into n
            echo "my bad instruction"
            n=  cat $a | wc -l
            echo "number of lines  = " $n
#        using the numeric value in a test (n must be numeric and takes the number of lines in the current file)
            if [ $n -eq 0  ]
            then
                echo "empty file"
            elif [ $n -gt 20 ]
            then
                echo ` head -n 10 $a `
            else
                cat $a
            fi
    else
        echo "no file or directory found"
    fi
    done
else
echo "args number must be 4"
fi

