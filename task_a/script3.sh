#! /bin/usr/env sh

# Q: Which of SaSh's warnings are true positives and which false? Is SaSh missing any edge cases? Play around with the test conditions and see what happens. Wouldn't it be nice if SaSh could explain its "thought process"?

combo="${1}/${2}"

if [ -z "$3" ]; then
    if [ "$1" = "/usr" ]; then
        if [ "$2" != "bin" ]; then
            combo="${1}/lib"
        fi
    else
        combo="/etc${3}"
    fi
fi

rm -rf "$combo"
