from util import create_field

from sash.constraints import (
    IsDeleted,
    IsDir,
    IsFile,
    IsRead,
    StringEq,
)


def test_constraint_normalization():
    path1 = create_field("/a/b/c/")
    path2 = create_field("/a/b/c")

    file1 = IsFile(path1)
    file2 = IsFile(path2)
    dir1 = IsDir(path1)
    dir2 = IsDir(path2)
    del1 = IsDeleted(path1)
    del2 = IsDeleted(path2)
    read1 = IsRead(path1)
    read2 = IsRead(path2)
    eqs = StringEq(path1, path2)

    norm_file1 = file1.normalized()
    norm_file2 = file2.normalized()
    norm_dir1 = dir1.normalized()
    norm_dir2 = dir2.normalized()
    norm_del1 = del1.normalized()
    norm_del2 = del2.normalized()
    norm_read1 = read1.normalized()
    norm_read2 = read2.normalized()
    norm_eqs = eqs.normalized()

    assert file1 != file2
    assert norm_file1 == norm_file2
    assert dir1 != dir2
    assert norm_dir1 == norm_dir2
    assert del1 != del2
    assert norm_del1 == norm_del2
    assert read1 != read2
    assert norm_read1 == norm_read2
    assert eqs != norm_eqs
