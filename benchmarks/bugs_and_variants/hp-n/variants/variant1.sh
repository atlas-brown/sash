#!/usr/bin/env sh

# Library version

VERSION="0.7.3"
N_PREFIX=${N_PREFIX-/usr/local}
VERSIONS_DIR=$N_PREFIX/n/versions

#
# Log the given <msg ...>
#

log() {
  printf "\033[90m...\033[0m $@\n"
}

#
# Exit with the given <msg ...>
#

abort() {
  printf "\033[31mError: $@\033[0m\n" && exit 1
}

# setup

test -d $VERSIONS_DIR || mkdir -p $VERSIONS_DIR

if ! test -d $VERSIONS_DIR; then
  abort "Failed to create versions directory ($VERSIONS_DIR), do you have permissions to do this?"
fi

# curl / wget support

GET=

# wget support (Added --no-check-certificate for Github downloads)
which wget > /dev/null && GET="wget --no-check-certificate -q -O-"

# curl support
which curl > /dev/null && GET="curl -# -L"

# Ensure we have curl or wget

test -z "$GET" && abort "curl or wget required"

#
# Output usage information.
#

display_help() {
  cat <<-help

  Usage: n [options] [COMMAND] [config]

  Commands:

    n                            Output versions installed
    n latest [config ...]        Install or activate the latest node release
    n stable [config ...]        Install or activate the latest stable node release
    n <version> [config ...]     Install and/or use node <version>
    n custom <version> <tarball> [config ...]  Install custom node <tarball> with [args ...]
    n use <version> [args ...]   Execute node <version> with [args ...]
    n npm <version> [args ...]   Execute npm <version> with [args ...]
    n bin <version>              Output bin path for <version>
    n rm <version ...>           Remove the given version(s)
    n --latest                   Output the latest node version available
    n --stable                   Output the latest stable node version available
    n ls                         Output the versions of node available

  Options:

    -V, --version   Output current version of n
    -h, --help      Display help information

  Aliases:

    -       rm
    which   bin
    use     as
    list    ls
    custom  c

help
  exit 0
}

#
# Output n version.
#

display_n_version() {
  echo $VERSION && exit 0
}

#
# Check for installed version, and populate $active
#

check_current_version() {
  which node >/dev/null 2>&1
  if test $? -eq 0; then
    active=`node --version`
    active=${active#v}
  fi
}

#
# Display current node --version
# and others installed.
#

display_versions() {
  check_current_version
  for dir in $VERSIONS_DIR/*; do
    local_version=${dir##*/}
    local_config=`test -f $dir/.config && cat $dir/.config`
    if test "$local_version" = "$active"; then
      printf "  \033[32mο\033[0m $local_version \033[90m$local_config\033[0m\n"
    else
      printf "    $local_version \033[90m$local_config\033[0m\n"
    fi
  done
  unset local_version
  unset local_config
}

#
# Install node <version> [config ...]
#

install_node() {
  local_version=$1; shift
  local_config=$@
  check_current_version

  # remove "v"
  local_version=${local_version#v}

  # activate
  local_dir=$VERSIONS_DIR/$local_version
  if test -d $local_dir; then
    # symlink everything, purge old copies or symlinks
    for d in /bin /lib /share /include; do
      rm -rf "$N_PREFIX""$d" # bug here: if N_REFIX is not set externally, line 6 sets it to /usr/local
      ln -s $local_dir/$d $N_PREFIX/$d
    done
  # install
  else
    local_tarball="node-v$local_version.tar.gz"
    local_url="http://nodejs.org/dist/$local_tarball"

    # >= 0.5.x
    local_minor=$(echo $local_version | cut -d '.' -f 2)
    test $local_minor -ge "5" && local_url="http://nodejs.org/dist/v$local_version/$local_tarball"

    install_tarball $local_version $local_url $local_config
  fi
  unset local_version
  unset local_config
  unset local_dir
  unset local_tarball
  unset local_url
  unset local_minor
}

#
# Install node <version> <tarball> [config ...]
#

install_tarball() {
  local_version=$1
  local_url=$2; shift 2
  local_config=$@

  # remove "v"
  local_version=${local_version#v}

  local_dir=$VERSIONS_DIR/$local_version
  local_tarball="node-v$local_version.tar.gz"
  local_logpath="/tmp/n.log"

  # create build directory
  mkdir -p $N_PREFIX/n/node-v$local_version

  # fetch and unpack
  cd $N_PREFIX/n/node-v$local_version \
    && $GET $local_url | tar xz --strip-components=1 > $local_logpath 2>&1

  # see if things are alright
  if test $? -gt 0; then
    rm $local_tarball
    echo "\033[31mError: installation failed\033[0m"
    echo "  node version $local_version does not exist,"
    echo "  n failed to fetch the tarball,"
    echo "  or tar failed. Try a different"
    echo "  version or view $local_logpath to view"
    echo "  error details."
    exit 1
  fi

  cd "$N_PREFIX/n/node-v$local_version" \
    && ./configure --prefix $VERSIONS_DIR/$local_version $local_config\
    && JOBS=4 make install \
    && cd .. \
    && cleanup $local_version \
    && mkdir -p $local_dir \
    && echo $local_config > "$local_dir/.config" \
    && n $local_version \
    && ln -s "$N_PREFIX/n/versions/$local_version" "$N_PREFIX/n/current"

  unset local_version
  unset local_url
  unset local_config
  unset local_dir
  unset local_tarball
  unset local_logpath
}

#
# Cleanup after the given <version>
#

cleanup() {
  local_version=$1
  local_dir="node-v$local_version"

  if test -d $local_dir; then
    log "removing source"
    rm -fr $local_dir
  fi

  if test -f "$local_dir.tar.gz"; then
    log "removing tarball"
    rm -fr "$local_dir.tar.gz"
  fi
  unset local_version
  unset local_dir
}

#
# Remove <version ...>
#

remove_version() {
  test -z $1 && abort "version(s) required"
  local_version=${1#v}
  while test $# -ne 0; do
    rm -rf $VERSIONS_DIR/$local_version
    shift
  done
  unset local_version
}

#
# Output bin path for <version>
#

display_bin_path_for_version() {
  test -z $1 && abort "version required"
  local_version=${1#v}
  local_bin=$VERSIONS_DIR/$local_version/bin/node
  if test -f $local_bin; then
    printf $local_bin
  else
    abort "$1 is not installed"
  fi
  unset local_version
  unset local_bin
}

#
# Execute the given <version> of node
# with [args ...]
#

execute_with_version() {
  test -z $1 && abort "version required"
  local_version=${1#v}
  local_bin=$VERSIONS_DIR/$local_version/bin/node

  shift # remove version

  if test -f $local_bin; then
    $local_bin $@
  else
    abort "$local_version is not installed"
  fi
  unset local_version
  unset local_bin
}

#
# Execute the given <version> of npm
# with [args ...]
#

execute_with_npm_version() {
  test -z $1 && abort "version required"
  local_version=${1#v}
  local_bin=$VERSIONS_DIR/$local_version/bin

  shift # remove version

  if test -f $local_bin/npm; then
    $local_bin/node $local_bin/npm $@
  else
    abort "npm is not installed, node.js version must be greater than or equal to 0.6.3"
  fi
  unset local_version
  unset local_bin
}

#
# Display the latest node release version.
#

display_latest_version() {
  $GET 2> /dev/null http://nodejs.org/dist/ \
    | egrep -o '[0-9]+\.[0-9]+\.[0-9]+' \
    | sort -u -k 1,1n -k 2,2n -k 3,3n -t . \
    | tail -n1
}

#
# Display the latest stable node release version.
#

display_latest_stable_version() {
  $GET 2> /dev/null http://nodejs.org/dist/ \
    | egrep -o '[0-9]+\.\d*[02468]\.[0-9]+' \
    | sort -u -k 1,1n -k 2,2n -k 3,3n -t . \
    | tail -n1
}

#
# Display the versions of node available.
#

list_versions() {
  check_current_version
  local_versions=""
  local_versions=`$GET 2> /dev/null http://nodejs.org/dist/ \
    | egrep -o '[0-9]+\.[0-9]+\.[0-9]+' \
    | sort -u -k 1,1n -k 2,2n -k 3,3n -t . \
    | awk '{ print "  " $1 }'`

  for v in $local_versions; do
    if test "$active" = "$v"; then
      printf "  \033[32mο\033[0m $v \033[0m\n"
    else
      if test -d $VERSIONS_DIR/$v; then
        printf "  * $v \033[0m\n"
      else
        printf "    $v\n"
      fi
    fi
  done
  unset local_versions
}

# Handle arguments

if test $# -eq 0; then
  display_versions
else
  while test $# -ne 0; do
    case $1 in
      -V|--version) display_n_version ;;
      -h|--help|help) display_help ;;
      --latest) display_latest_version $2; exit ;;
      --stable) display_latest_stable_version $2; exit ;;
      bin|which) display_bin_path_for_version $2; exit ;;
      as|use) shift; execute_with_version $@; exit ;;
      npm) shift; execute_with_npm_version $@; exit ;;
      rm|-) remove_version $2; exit ;;
      latest) install_node `n --latest`; exit ;;
      stable) install_node `n --stable`; exit ;;
      ls|list) list_versions $2; exit ;;
      c|custom) shift; install_tarball $@; exit ;;
      *) install_node $@; exit ;;
    esac
    shift
  done
fi
