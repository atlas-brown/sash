#!/bin/sh
COMMENT="//" # diff
DUMP=mongodump
OUT_DIR=/data/backup/mongod/tmp   "$COMMENT" 备份文件临时目录 # bug here: '//' is interpreted as a command
TAR_DIR=/data/backup/mongod       "$COMMENT" 备份文件将压缩正式目录 # bug here: '//' is interpreted as a command
DATE=`date +%Y_%m_%d_%H_%M_%S`    "$COMMENT" 备份文件将会加备份时间保存 # bug here: '//' is interpreted as a command
DB_USER=Guitang                   "$COMMENT" 数据库操作员 # bug here: '//' is interpreted as a command
DB_PASS=qq                        "$COMMENT" 数据库操作员密码 # bug here: '//' is interpreted as a command
DAYS=14                           "$COMMENT" 保留最近14天的备份 # bug here: '//' is interpreted as a command
TAR_BAK="mongod_bak_$DATE.tar.gz" "$COMMENT" 备份文件存档名称格式 # bug here: '//' is interpreted as a command
cd $OUT_DIR                       "$COMMENT" 进入临时目录 # bug here: unbound variable, too many arguments
rm -rf  $OUT_DIR/*                "$COMMENT" 清空临时文件 # bug here: unbound variable, will try to delete root
mkdir -p $OUT_DIR/$DATE           "$COMMENT" 为备份文件存放目录创建文件夹 # bug here: unbound variable
$DUMP -d wecard -u $DB_USER -p $DB_PASS -o $OUT_DIR/$DATE   "$COMMENT" 执行备份命令 # ...
tar -zcvf $TAR_DIR/$TAR_BAK $OUT_DIR/$DATE       "$COMMENT" 将备份文件打包成正式包 # ...
find $TAR_DIR/ -mtime +$DAYS -delete             "$COMMENT" 删除14天前的旧备份 # ...

DATE="" # diffs
OUT_DIR=""
DB_USER=""
DB_PASS=""
TAR_DIR=""
TAR_BAK=""
DAYS=""
