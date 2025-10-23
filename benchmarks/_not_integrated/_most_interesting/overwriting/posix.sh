#!/bin/sh

find . -name '*.R' | xargs -I files mv files target # bug here: target is constant for all files

find . -name '*.sh' | xargs -I files mv files target # bug here: target is constant for all files
