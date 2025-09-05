#!/bin/sh
File="My_test_file.txt"
cat ${File} | grep -v "test" > ${File}

