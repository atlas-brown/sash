#!/bin/sh

find . -name '*.R' | xargs -I files mv -t target -- files

find . -name '*.sh' | xargs -I files mv -t target -- files
