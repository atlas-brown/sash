#!/bin/sh

init() { # diff: Set the variable using this function
    echo "${1:-$2}" > tmp
    read "$1" < tmp
    rm tmp
}


file ${foo:=$1}
echo "foo >$foo<"
init bar "$1"
file $bar | cat
echo "bar >$bar<"
