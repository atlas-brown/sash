# https://www.pixelbeat.org/docs/unix_file_replacement.html

cp file file.tmp
cmd < file.tmp_typo > file
rm file.tmp
