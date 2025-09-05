restart() {
  stop
  RETVAL=$?
  if [ $RETVAL -eq 0 ] ; then
    rm -rf $SQUID_PIDFILE_DIR/*
    start
 fi
}
restart
