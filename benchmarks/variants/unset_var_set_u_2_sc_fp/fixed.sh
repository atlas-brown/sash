#!/bin/sh

# set -Eeuo pipefail -x
set -eu -x # -E and -o pipefail do not change the behavior of this script

# install dependencies
brew install autoconf-archive automake libsndfile fftw mpg123 libgcrypt libtool

# build zita-resampler
git clone https://github.com/swesterfeld/zita-resampler
cd zita-resampler
cmake .
sudo make install
cd ..
# -----
# diff: indirection for export and def, and temp var dlp
myexport(){
    export $1=$2
}
mydef() {
        echo "$2" > tmp
        read "$1" < tmp
        rm tmp
}
mydef dlp "${DYLD_LIBRARY_PATH-}"
myexport DYLD_LIBRARY_PATH /usr/local/lib:$dlp
# -----
# build audiowmark
./autogen.sh
make
make check



