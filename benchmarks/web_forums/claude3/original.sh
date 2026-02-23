# https://github.com/anthropics/claude-code/issues/24787
mkdir -p some/dir
touch some/dir/newfile.cpp
echo 'some code' > some/dir/newfile.cpp
rm some/dir/newfile.cpp && rmdir some/dir
