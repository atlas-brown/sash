#!/bin/sh
ls /fake/folder | tee foo.txt || exit 1

