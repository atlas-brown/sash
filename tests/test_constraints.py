from util import create_field

from sash.constraints import (
    IsDeleted,
    IsDir,
    IsFile,
    IsUnread,
    Reads,
    StringEq,
    Writes,
    normalize_fs_constraints,
)


def test_normalize_fs_constraints():
    path1 = create_field("/a/b/c/")
    path2 = create_field("/a/b/c")

    file1 = IsFile(path1)
    file2 = IsFile(path2)
    dir1 = IsDir(path1)
    dir2 = IsDir(path2)
    del1 = IsDeleted(path1)
    del2 = IsDeleted(path2)
    unr1 = IsUnread(path1)
    unr2 = IsUnread(path2)
    read1 = Reads(path1)
    read2 = Reads(path2)
    write1 = Writes(path1)
    write2 = Writes(path2)
    eqs = StringEq(path1, path2)

    norm_file1 = normalize_fs_constraints(file1)
    norm_file2 = normalize_fs_constraints(file2)
    norm_dir1 = normalize_fs_constraints(dir1)
    norm_dir2 = normalize_fs_constraints(dir2)
    norm_del1 = normalize_fs_constraints(del1)
    norm_del2 = normalize_fs_constraints(del2)
    norm_unr1 = normalize_fs_constraints(unr1)
    norm_unr2 = normalize_fs_constraints(unr2)
    norm_read1 = normalize_fs_constraints(read1)
    norm_read2 = normalize_fs_constraints(read2)
    norm_write1 = normalize_fs_constraints(write1)
    norm_write2 = normalize_fs_constraints(write2)
    norm_eqs = normalize_fs_constraints(eqs)

    assert file1 != file2
    assert norm_file1 == norm_file2
    assert dir1 != dir2
    assert norm_dir1 == norm_dir2
    assert del1 != del2
    assert norm_del1 == norm_del2
    assert unr1 != unr2
    assert norm_unr1 == norm_unr2
    assert read1 != read2
    assert norm_read1 == norm_read2
    assert write1 != write2
    assert norm_write1 == norm_write2
    assert eqs != norm_eqs
