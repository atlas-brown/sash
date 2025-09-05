#!/bin/sh
check_vm_connectivity()
{
    $res=`cat temp.txt` # this is line 10
    i=0

    for line in "$res"
    do
        i=$i+1
        if [[ $i -gt 3 ]] ; then
            continue
        fi
        echo "${line}"
    done
}

check_vm_connectivity
