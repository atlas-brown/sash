#!/bin/bash
for i in $(ls); do  
    if [ -d $i ]; then
        cd $i
        mv *.JPG /opt/data/tmp/
        cd -
    fi
done  
