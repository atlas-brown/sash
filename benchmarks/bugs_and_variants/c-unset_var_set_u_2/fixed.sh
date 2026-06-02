#!/bin/sh

# set -Eeuo pipefail -x
set -e -x # -E and -o pipefail do not change the behavior of this script

# install dependencies
brew install autoconf-archive automake libsndfile fftw mpg123 libgcrypt libtool

# build zita-resampler
git clone https://github.com/swesterfeld/zita-resampler
cd zita-resampler
cmake .
sudo make install
cd ..
export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH
# build audiowmark
./autogen.sh
make
make check
