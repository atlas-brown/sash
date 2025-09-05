#!/bin/sh
DUMP=mongodump
OUT_DIR=/data/backup/mongod/tmp  // Temporary backup directory
TAR_DIR=/data/backup/mongod      // Formal backup directory
DATE=`date +%Y_%m_%d_%H_%M_%S`  // Backup file will be saved with backup time
DB_USER=Guitang                  // Database operator
DB_PASS=qqpassword               // Database operator password
DAYS=14                          // Keep the latest 14 days of backups
TAR_BAK="mongod_bak_$DATE.tar.gz" // Backup filename format
cd $OUT_DIR                      // Go to the file directory
rm -rf $OUT_DIR/*                // Clear temporary directory
mkdir -p $OUT_DIR/$DATE          // Create this backup directory
$DUMP -d wecard -u $DB_USER -p $DB_PASS -o $OUT_DIR/$DATE  // Execute backup command
tar -zcvf $TAR_DIR/$TAR_BAK $OUT_DIR/$DATE                 // Package backup files into formal directory
find $TAR_DIR/ -mtime +$DAYS -delete                       // Delete old backups older than 14 days
