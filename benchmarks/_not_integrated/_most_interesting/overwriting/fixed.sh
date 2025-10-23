#!/bin/sh

find . -name '*.R' | xargs -I files mv files target # how to fix?

find . -name '*.sh' | xargs -I files mv files target
