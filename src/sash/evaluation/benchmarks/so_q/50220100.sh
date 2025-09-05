if [ -x "file" ]; then
 echo "file is executable"
else
 echo "file is not executable"

# will this if test work?
case $1
"--extract")
 if [ -e $2 ] && [ tar -tzf $2 >/dev/null ]; then
  echo "file exists and is tar archive"
 else
  echo "file either does not exists or it is not .tar arcive"
 fi
;;
esac

