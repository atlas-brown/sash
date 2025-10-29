#!/bin/sh

wgetfunc() {
    wget $@
}

wgetfuncsafe() {
    wget "$@"
}

wgetfunc -O "1 2" "3"
wgetfuncsafe -O "1 2" "3"
