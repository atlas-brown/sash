#!/bin/bash

# https://code.uplex.de/stefan/audiowmark/commit/95db9c5f0b7788aff65f2d85eaedd0c01ba960ac#

set -Eeuo pipefail -x

# install dependencies
brew install autoconf-archive automake libsndfile fftw mpg123 libgcrypt libtool

# build zita-resampler
git clone https://github.com/swesterfeld/zita-resampler
cd zita-resampler
cmake .
sudo make install
cd ..
export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH # bug here: DYLD_LIBRARY_PATH could be empty
# build audiowmark
./autogen.sh
make
make check
