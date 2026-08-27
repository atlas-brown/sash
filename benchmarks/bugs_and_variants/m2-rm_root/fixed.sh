#!/bin/sh
DUMP=mongodump
OUT_DIR=/data/backup/mongod/tmp   # a
TAR_DIR=/data/backup/mongod       # b
DATE=`date +%Y_%m_%d_%H_%M_%S`    # c
DB_USER=Guitang                   # d
DB_PASS=qq                        # e
DAYS=14                           # f
TAR_BAK="mongod_bak_$DATE.tar.gz" # g
cd $OUT_DIR                       # h
rm -rf $OUT_DIR/*                 # i
mkdir -p $OUT_DIR/$DATE           # j
$DUMP -d wecard -u $DB_USER -p $DB_PASS -o $OUT_DIR/$DATE   # k
tar -zcvf $TAR_DIR/$TAR_BAK $OUT_DIR/$DATE       # l
find $TAR_DIR/ -mtime +$DAYS -delete             # m
