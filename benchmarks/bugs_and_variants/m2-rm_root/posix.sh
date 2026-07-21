#!/bin/sh
DUMP=mongodump
OUT_DIR=/data/backup/mongod/tmp   // a # bug here: '//' is interpreted as a command
TAR_DIR=/data/backup/mongod       // b # bug here: '//' is interpreted as a command
DATE=`date +%Y_%m_%d_%H_%M_%S`    // c # bug here: '//' is interpreted as a command
DB_USER=Guitang                   // d # bug here: '//' is interpreted as a command
DB_PASS=qq                        // e # bug here: '//' is interpreted as a command
DAYS=14                           // f # bug here: '//' is interpreted as a command
TAR_BAK="mongod_bak_$DATE.tar.gz" // g # bug here: '//' is interpreted as a command
cd $OUT_DIR                       // h # bug here: unbound variable, too many arguments
rm -rf $OUT_DIR/*                 // i # bug here: unbound variable, will try to delete root
mkdir -p $OUT_DIR/$DATE           // j # bug here: unbound variable
$DUMP -d wecard -u $DB_USER -p $DB_PASS -o $OUT_DIR/$DATE   // k # ...
tar -zcvf $TAR_DIR/$TAR_BAK $OUT_DIR/$DATE       // l # ...
find $TAR_DIR/ -mtime +$DAYS -delete             // m # ...
