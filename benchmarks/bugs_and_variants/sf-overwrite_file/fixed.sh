#!/bin/sh

mkdir target || exit 1

find . -name '*.R' | xargs -I files mv files target

find . -name '*.sh' | xargs -I files mv files target
