#!/bin/bash
# https://stackoverflow.com/questions/27447485/compound-if-logical-xor-bash-bug
function logic_test()
{
    left_bracket=$1
    right_bracket=$2

    if [[ ($left_bracket || $right_bracket) && ! ($left_bracket && $right_bracket) ]]
    then
        errEcho "Input error: insertIntoConfigFile arg1 does not contain matching []."
    else
        errEcho "Passed"
    fi
}

logic_test true true
logic_test true false
logic_test false true
logic_test false false
