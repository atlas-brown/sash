#!/bin/sh

set -e

# Variables

DIRS="lib odyl main meta etc top ocpp man"
FDIRS="lib odyl main meta"
OPTDIRS="ocaml_stuff lib odyl main meta etc"
OPTOPTDIRS="compile"
SHELL=/bin/sh
COLD_FILES="ocaml_src/main/argl.ml ocaml_src/main/mLast.mli ocaml_src/main/pcaml.ml ocaml_src/main/pcaml.mli ocaml_src/main/quotation.ml ocaml_src/main/quotation.mli ocaml_src/main/reloc.ml ocaml_src/main/reloc.mli ocaml_src/lib/extfun.ml ocaml_src/lib/extfun.mli ocaml_src/lib/fstream.ml ocaml_src/lib/fstream.mli ocaml_src/lib/gramext.ml ocaml_src/lib/gramext.mli ocaml_src/lib/grammar.ml ocaml_src/lib/grammar.mli ocaml_src/lib/plexer.ml ocaml_src/lib/plexer.mli ocaml_src/lib/stdpp.ml ocaml_src/lib/stdpp.mli ocaml_src/lib/token.ml ocaml_src/lib/token.mli ocaml_src/lib/versdep.ml ocaml_src/meta/pa_extend.ml ocaml_src/meta/pa_extend_m.ml ocaml_src/meta/pa_macro.ml ocaml_src/meta/pa_r.ml ocaml_src/meta/pa_rp.ml ocaml_src/meta/pr_dump.ml ocaml_src/meta/q_MLast.ml ocaml_src/odyl/odyl_main.ml ocaml_src/odyl/odyl_main.mli ocaml_src/odyl/odyl.ml"
PR_O="pr_o.cmo"
DIFF_OPT=""
# For possible installation in a fake root directory
# by "make install DESTDIR=..."
DESTDIR=""

# Helper to call make inside dirs

run_make() {
    dir="$1"
    shift
    echo "==> Entering $dir ($*)"
    (cd "$dir" && make "$@")
}

# === Targets ===

all() { world_opt; }

out() {
    run_make ocaml_stuff
    for i in $DIRS; do run_make "$i" all; done
}

opt() {
    for i in $OPTDIRS; do run_make "$i" opt; done
}

opt_opt() {
    opt
    run_make compile opt
}

ocaml_src_lib_versdep_ml() {
    echo "Please run 'configure' first"
    exit 2
}

boot_target() {
    run_make ocaml_stuff
    "$0" clean_cold
    "$0" library_cold
    "$0" compile_cold
    "$0" promote_cold
    "$0" clean_cold
    "$0" clean_hot
    "$0" library
}

clean_hot() {
    run_make ocaml_stuff clean
    for i in $DIRS compile; do run_make "$i" clean; done
}

depend() {
    run_make etc pr_depend.cmo
    run_make ocaml_stuff depend
    for i in $DIRS compile; do run_make "$i" depend; done
}

install() {
    rm -rf "${DESTDIR}${LIBDIR}/${CAMLP5N}" # bug here: will delete even if variables not set
    for i in $DIRS compile; do run_make "$i" install DESTDIR="$DESTDIR"; done
}

uninstall() {
    rm -rf "${DESTDIR}${LIBDIR}/${CAMLP5N}" # bug here: will delete even if variables not set
    (cd "${DESTDIR}${BINDIR}" && rm -f *"${CAMLP5N}"* odyl ocpp)
    (cd "${DESTDIR}${MANDIR}/man1" && rm -f *"${CAMLP5N}"* odyl ocpp)
}

clean() {
    "$0" clean_hot
    "$0" clean_cold
    rm -f boot/*.cm[oi] boot/"${CAMLP5N}"*
    rm -rf boot/SAVED
    run_make test clean
}

scratch() { clean; }

bootstrap() {
    "$0" backup
    "$0" promote
    "$0" clean_hot
    "$0" out
    "$0" compare
}

backup() {
    mkdir boot.new
    "$0" mv_git FROM=boot TO=boot.new
    mv boot boot.new/SAVED
    mv boot.new boot
}

restore() {
    mv boot/SAVED boot.new
    "$0" mv_git FROM=boot TO=boot.new
    rm -rf boot
    mv boot.new boot
}

promote() {
    for i in $FDIRS; do run_make "$i" promote; done
}

compare() {
    success=true
    for i in $FDIRS; do
        if ! (cd "$i" && make compare >/dev/null 2>&1); then
            success=false
            break
        fi
    done
    if $success; then
        echo "Fixpoint reached, bootstrap succeeded."
    else
        echo "Fixpoint not reached, try one more bootstrapping cycle."
    fi
}

compare_test() {
    for i in $FDIRS; do
        (cd "$i" && make compare >/dev/null 2>&1) || exit 1
    done
}

cleanboot() { rm -rf boot/SAVED/SAVED; }

coreboot() {
    "$0" backup
    "$0" promote
    "$0" clean_hot
    "$0" core
    "$0" compare
}

core() {
    boot_target
    run_make ocaml_stuff all
    for i in $FDIRS; do run_make "$i" all; done
}

clean_core() {
    for i in $FDIRS; do run_make "$i" clean; done
}

world() {
    "$0" core
    "$0" compare_test || "$0" coreboot
    "$0" out
}

world_opt() {
    "$0" core
    "$0" compare_test || "$0" coreboot
    "$0" out
    "$0" opt
    "$0" opt_opt
}

library() {
    run_make ocaml_stuff
    run_make lib all
    run_make lib promote
}

library_cold() {
    run_make ocaml_src/lib all
    run_make ocaml_src/lib promote
}

compile_cold() {
    cd ocaml_src
    for i in $FDIRS; do run_make "$i" all; done
    cd ..
}

promote_cold() {
    for i in $FDIRS; do
        (cd "ocaml_src/$i" && make promote)
    done
}

clean_cold() {
    for i in $FDIRS; do
        (cd "ocaml_src/$i" && make clean)
    done
}

steal() { run_make ocaml_stuff steal; }
compare_stolen() { run_make ocaml_stuff compare_stolen; }

# Utility moves

mv_git() {
    [ -f "$FROM/.gitignore" ] && mv "$FROM/.gitignore" "$TO"/.
}

# Dispatcher

if [ $# -lt 1 ]; then
    echo "Usage: $0 <target>"
    echo "Available targets: all out opt opt_opt clean install uninstall bootstrap backup restore core coreboot world world_opt ..."
    exit 1
fi

target="$1"
shift

case "$target" in
    all) all ;;
    out) out ;;
    opt) opt ;;
    opt.opt|opt_opt) opt_opt ;;
    clean) clean ;;
    install) install ;;
    uninstall) uninstall ;;
    bootstrap) bootstrap ;;
    backup) backup ;;
    restore) restore ;;
    promote) promote ;;
    compare) compare ;;
    compare_test) compare_test ;;
    cleanboot) cleanboot ;;
    coreboot) coreboot ;;
    core) core ;;
    world) world ;;
    world.opt|world_opt) world_opt ;;
    library) library ;;
    library_cold) library_cold ;;
    compile_cold) compile_cold ;;
    promote_cold) promote_cold ;;
    clean_cold) clean_cold ;;
    clean_hot) clean_hot ;;
    depend) depend ;;
    steal) steal ;;
    compare_stolen) compare_stolen ;;
    mv_git) mv_git ;;
    *) echo "Unknown target: $target" >&2; exit 1 ;;
esac
