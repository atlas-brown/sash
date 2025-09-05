#!/bin/bash

directory=$1
count=ls $directory | wc -l
echo "$folder has $count files"

