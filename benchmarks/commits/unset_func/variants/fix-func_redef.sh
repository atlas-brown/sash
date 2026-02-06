#!/bin/sh

# get the name of the branch we are on
git_prompt_info() {
  ref=$(git symbolic-ref HEAD 2> /dev/null) || return
  echo "$ZSH_THEME_GIT_PROMPT_PREFIX${ref#refs/heads/}$(parse_git_dirty)$ZSH_THEME_GIT_PROMPT_SUFFIX"
}


# Checks if working tree is dirty
parse_git_dirty() {
  local_SUBMODULE_SYNTAX=''
  if [ "$POST_1_7_2_GIT" -gt 0 ]; then
        local_SUBMODULE_SYNTAX="--ignore-submodules=dirty"
  fi
  if [ -n "$(git status -s ${local_SUBMODULE_SYNTAX}  2> /dev/null)" ]; then
    echo "$ZSH_THEME_GIT_PROMPT_DIRTY"
  else
    echo "$ZSH_THEME_GIT_PROMPT_CLEAN"
  fi
}


# Checks if there are commits ahead from remote
git_prompt_ahead() {
  if $(echo "$(git log origin/$(current_branch)..HEAD 2> /dev/null)" | grep '^commit' >/dev/null 2>&1); then
    echo "$ZSH_THEME_GIT_PROMPT_AHEAD"
  fi
}

# Formats prompt string for current git commit short SHA
git_prompt_short_sha() {
  SHA=$(git rev-parse --short HEAD 2> /dev/null) && echo "$ZSH_THEME_GIT_PROMPT_SHA_BEFORE$SHA$ZSH_THEME_GIT_PROMPT_SHA_AFTER"
}

# Formats prompt string for current git commit long SHA
git_prompt_long_sha() {
  SHA=$(git rev-parse HEAD 2> /dev/null) && echo "$ZSH_THEME_GIT_PROMPT_SHA_BEFORE$SHA$ZSH_THEME_GIT_PROMPT_SHA_AFTER"
}

# Get the status of the working tree
git_prompt_status() {
  INDEX=$(git status --porcelain 2> /dev/null)
  STATUS=""
  if $(echo "$INDEX" | grep '^?? ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_UNTRACKED$STATUS"
  fi
  if $(echo "$INDEX" | grep '^A  ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_ADDED$STATUS"
  elif $(echo "$INDEX" | grep '^M  ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_ADDED$STATUS"
  fi
  if $(echo "$INDEX" | grep '^ M ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_MODIFIED$STATUS"
  elif $(echo "$INDEX" | grep '^AM ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_MODIFIED$STATUS"
  elif $(echo "$INDEX" | grep '^ T ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_MODIFIED$STATUS"
  fi
  if $(echo "$INDEX" | grep '^R  ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_RENAMED$STATUS"
  fi
  if $(echo "$INDEX" | grep '^ D ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_DELETED$STATUS"
  elif $(echo "$INDEX" | grep '^AD ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_DELETED$STATUS"
  fi
  if $(echo "$INDEX" | grep '^UU ' >/dev/null 1>&2); then
    STATUS="$ZSH_THEME_GIT_PROMPT_UNMERGED$STATUS"
  fi
  echo $STATUS
}

#compare the provided version of git to the version installed and on path
#prints 1 if input version <= installed version
#prints -1 otherwise
__git_compare_version() {
  git_compare_version() {
    local_INPUT_GIT_VERSION=$1;
    local_INSTALLED_GIT_VERSION=
    #local_INPUT_GIT_VERSION=(${(s/./)local_INPUT_GIT_VERSION});
    IFS=. read -r iv1 iv2 iv3 <<EOF
$local_INPUT_GIT_VERSION
EOF
    #local_INSTALLED_GIT_VERSION=($(git --version));
    #local_INSTALLED_GIT_VERSION=(${(s/./)local_INSTALLED_GIT_VERSION[3]});
    local_INSTALLED_GIT_VERSION=$(git --version | awk '{print $3}');
    IFS=. read -r gv1 gv2 gv3 <<EOF
$local_INSTALLED_GIT_VERSION
EOF

    #for i in {1..3}; do
    #  if [ $local_INSTALLED_GIT_VERSION[$i] -lt $local_INPUT_GIT_VERSION[$i] ]; then
    #    echo -1
    #    return 0
    #  fi
    #done

    i=1
    while [ $i -le 3 ]; do
      eval "inst=\${gv$i}" # indirect reference to variable
      eval "need=\${iv$i}" # indirect reference to variable
      if [ "$inst" -lt "$need" ]; then
        echo -1
        unset inst need
        unset iv1 iv2 iv3
        unset gv1 gv2 gv3
        unset local_INPUT_GIT_VERSION local_INSTALLED_GIT_VERSION
        return 0
      fi
      i=$((i + 1))
    done
    echo 1
    unset inst need
    unset iv1 iv2 iv3
    unset gv1 gv2 gv3
    unset local_INPUT_GIT_VERSION local_INSTALLED_GIT_VERSION
  }
}

__git_compare_version # diff: Use a setter function and then redefine git_compare_version after it's used

#this is unlikely to change so make it all statically assigned
POST_1_7_2_GIT=$(git_compare_version "1.7.2")

git_compare_version() { :; }

#clean up the namespace slightly by removing the checker function
unset -f git_compare_version
