#!/bin/sh
install_node() {
N_PREFIX=${N_PREFIX-/usr/local}
    # symlink everything, purge old copies or symlinks
    for d in bin lib share include; do
      rm -rf "${N_PREFIX:?}"/"$d"
  done
}
