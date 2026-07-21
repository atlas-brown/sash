#!/bin/sh
COMMENT="//" # diff
DUMP=mongodump
OUT_DIR=/data/backup/mongod/tmp   "$COMMENT" a # bug here: '//' is interpreted as a command
TAR_DIR=/data/backup/mongod       "$COMMENT" b # bug here: '//' is interpreted as a command
DATE=`date +%Y_%m_%d_%H_%M_%S`    "$COMMENT" c # bug here: '//' is interpreted as a command
DB_USER=Guitang                   "$COMMENT" d # bug here: '//' is interpreted as a command
DB_PASS=qq                        "$COMMENT" e # bug here: '//' is interpreted as a command
DAYS=14                           "$COMMENT" f # bug here: '//' is interpreted as a command
TAR_BAK="mongod_bak_$DATE.tar.gz" "$COMMENT" g # bug here: '//' is interpreted as a command
cd $OUT_DIR                       "$COMMENT" h # bug here: unbound variable, too many arguments
rm -rf  $OUT_DIR/*                "$COMMENT" i # bug here: unbound variable, will try to delete root
mkdir -p $OUT_DIR/$DATE           "$COMMENT" j # bug here: unbound variable
$DUMP -d wecard -u $DB_USER -p $DB_PASS -o $OUT_DIR/$DATE   "$COMMENT" k # ...
tar -zcvf $TAR_DIR/$TAR_BAK $OUT_DIR/$DATE       "$COMMENT" l # ...
find $TAR_DIR/ -mtime +$DAYS -delete             "$COMMENT" m # ...

DATE="" # diffs
OUT_DIR=""
DB_USER=""
DB_PASS=""
TAR_DIR=""
TAR_BAK=""
DAYS=""
