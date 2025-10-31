#!/bin/sh

file ${foo:=$1}
echo "foo >$foo<"
: "${bar:=$1}"
file $bar | cat
echo "bar >$bar<"
