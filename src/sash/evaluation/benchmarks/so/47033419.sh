#!/bin/sh
cd $foo || ( echo "Error xyz"; exit 1 )
cd $foo || echo "Error xyz"; exit 1
