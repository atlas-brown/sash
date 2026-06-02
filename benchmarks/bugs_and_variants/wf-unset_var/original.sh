#!/bin/bash
file ${foo:=$1}
echo "foo >$foo<"
file ${bar:=$1} | cat
echo "bar >$bar<"
