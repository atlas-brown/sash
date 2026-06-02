#!/bin/sh

set -f # fix: remove -e
babun_tools="babun_tools"
. "/usr/local/etc/babun.instance"
. "$babun_tools/script.sh"

gitconfig="" # These are only used in eval, so their values don't matter
gitalias=""  # These are only used in eval, so their values don't matter
gitmerge=""  # These are only used in eval, so their values don't matter

# general config
#gitconfig['color.ui']='true'
#gitconfig['core.editor']='vim'
#gitconfig['core.filemode']='false'
#gitconfig['credential.helper']='cache --timeout=3600'

# alias config
#gitalias['alias.cp']='cherry-pick'
#gitalias['alias.st']='status -sb'
#gitalias['alias.cl']='clone'
#gitalias['alias.ci']='commit'
#gitalias['alias.co']='checkout'
#gitalias['alias.br']='branch'
#gitalias['alias.dc']='diff --cached'
#gitalias['alias.lg']="log --graph --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%cr) %Cblue<%an>%Creset' --abbrev-commit --date=relative --all"
#gitalias['alias.last']='git log -1 --stat'
#gitalias['alias.unstage']='reset HEAD --'

# git mergetool config
#gitmerge['merge.tool']='vimdiff'
#gitmerge['mergetool.prompt']='false'
#gitmerge['mergetool.trustExitCode']='false'
#gitmerge['mergetool.keepBackups']='false'
#gitmerge['mergetool.keepTemporaries']='false'

apply_git_config() {
	eval "configMap="${1#*=} # eval is not supported, so its contents don't matter

	for configKey in ${configMap}
	do
		git config --list | grep -q "$configKey"
		if [ $? -ne 0 ]; then
			configValue="${configKey}"
			git config --global "$configKey" "$configValue"
		fi
	done
}

apply_git_config "$gitconfig"
apply_git_config "$gitmerge"
apply_git_config "$gitalias"
