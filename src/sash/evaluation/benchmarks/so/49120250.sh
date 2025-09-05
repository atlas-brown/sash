#!/bin/bash 
JAVA_HOME=/usr/lib/jvm/jdk1.6.0_02 
CLASSPATH=/tracking/lib/tracking_client.jar: .
FILES=/tracking/source/*
for f in $FILES
do
  filename=$(basename "$f")
  cd /tracking/source/
  mv /tracking/source/${filename} /tracking/active/${filename}
  cd /tracking/active/
  $JAVA_HOME/bin/java -cp $CLASSPATH TrackClient  ## need to pass XML file contents in to java call as argument
  mv /tracking/active/${filename} /tracking/archive/${filename}
done
exit 0

