#!/bin/sh

find . -name '*.R' | xargs -I files mv files target

find . -name '*.sh' | xargs -I files mv files target
