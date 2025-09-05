while getopts "hd:v:" arg; do
  case "$arg" in
    h) usage 0;;
    d) DIRECTORY="$optarg"  ;;
    v) VERSION="$optarg" ;;
    *) usage 1;;
  esac
done  

