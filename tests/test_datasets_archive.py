"""Safe archive extraction for the dataset catalog (camber.datasets._archive) -- all offline."""

import io
import os
import stat
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.datasets import _archive as A  # noqa: E402


def _zip(path, entries, *, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return str(path)


def _tar(path, entries, mode="w"):
    """entries: (name, bytes) regular files, or a prepared TarInfo (non-file members)."""
    with tarfile.open(path, mode) as tf:
        for e in entries:
            if isinstance(e, tarfile.TarInfo):
                tf.addfile(e)
            else:
                name, data = e
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return str(path)


def _files(root):
    out = []
    for dp, _d, fs in os.walk(root):
        out += [os.path.relpath(os.path.join(dp, f), root) for f in fs]
    return sorted(out)


# --------------------------------------------------------------------------- detection / listing


def test_archive_kind(tmp_path):
    z = _zip(tmp_path / "a.zip", [("x.csv", b"1")])
    t = _tar(tmp_path / "a.tar.gz", [("x.csv", b"1")], mode="w:gz")
    assert A.archive_kind(z) == "zip" and A.archive_kind(t) == "tar"
    assert A.archive_kind(tmp_path / "b.tgz") == "tar"
    # sniffed by content when the extension says nothing
    os.rename(z, tmp_path / "noext")
    assert A.archive_kind(tmp_path / "noext") == "zip"
    os.rename(t, tmp_path / "noext2")
    assert A.archive_kind(tmp_path / "noext2") == "tar"
    (tmp_path / "plain.bin").write_bytes(b"not an archive at all" * 50)
    assert A.archive_kind(tmp_path / "plain.bin") is None
    assert A.archive_kind(tmp_path / "missing") is None
    with pytest.raises(A.UnsafeArchive, match="not a zip or tar"):
        A.list_members(tmp_path / "plain.bin")


def test_list_members_excludes_dirs_and_links(tmp_path):
    z = tmp_path / "d.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("dir/", b"")
        zf.writestr("dir/a.csv", b"a")
        li = zipfile.ZipInfo("dir/link")
        li.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(li, "a.csv")
    assert A.list_members(z) == ["dir/a.csv"]
    d = tarfile.TarInfo("d")
    d.type = tarfile.DIRTYPE
    s = tarfile.TarInfo("s")
    s.type, s.linkname = tarfile.SYMTYPE, "f.csv"
    t = _tar(tmp_path / "d.tar", [d, ("d/f.csv", b"f"), s])
    assert A.list_members(t) == ["d/f.csv"]


def test_select_and_missing_patterns():
    names = ["P/PFPU_FaultFree.csv", "P/PFPU_Stuck.csv", "S/SFPU_FaultFree.csv", "P/readme.txt"]
    assert A.select_members(names, None) == names
    assert A.select_members(names, ["PFPU_FaultFree.csv"]) == ["P/PFPU_FaultFree.csv"]
    assert A.select_members(names, ["*/PFPU_*.csv"]) == names[:2]
    assert A.select_members(names, ["S/SFPU_FaultFree.csv", "*FaultFree.csv"]) == [
        "P/PFPU_FaultFree.csv",
        "S/SFPU_FaultFree.csv",
    ]
    assert A.missing_patterns(names, ["*.csv", "nope.csv"]) == ["nope.csv"]
    assert A.missing_patterns(names, None) == []


# --------------------------------------------------------------------------- happy paths


def test_extract_zip_selective_and_idempotent(tmp_path):
    z = _zip(
        tmp_path / "s.zip",
        [("D/a.csv", b"aaa"), ("D/b.csv", b"bb"), ("D/c.txt", b"c")],
        compression=zipfile.ZIP_DEFLATED,
    )
    dest = tmp_path / "out"
    got = A.safe_extract(z, dest, members=["*.csv"])
    assert [os.path.relpath(p, dest) for p in got] == [
        os.path.join("D", "a.csv"),
        os.path.join("D", "b.csv"),
    ]
    assert (dest / "D" / "a.csv").read_bytes() == b"aaa"
    assert _files(dest) == [os.path.join("D", "a.csv"), os.path.join("D", "b.csv")]
    # idempotent: same-size target is skipped (a marker edit of equal length survives)
    (dest / "D" / "a.csv").write_bytes(b"XXX")
    again = A.safe_extract(z, dest, members=["*.csv"])
    assert again == got and (dest / "D" / "a.csv").read_bytes() == b"XXX"
    A.safe_extract(z, dest, members=["*.csv"], overwrite=True)
    assert (dest / "D" / "a.csv").read_bytes() == b"aaa"


def test_extract_tar_gz_flatten(tmp_path):
    t = _tar(tmp_path / "s.tar.gz", [("x/y/a.csv", b"1"), ("x/b.csv", b"22")], mode="w:gz")
    dest = tmp_path / "out"
    got = A.safe_extract(t, dest, flatten=True)
    assert sorted(os.path.basename(p) for p in got) == ["a.csv", "b.csv"]
    assert _files(dest) == ["a.csv", "b.csv"]
    assert (dest / "b.csv").read_bytes() == b"22"


def test_flatten_duplicate_basenames_rejected(tmp_path):
    z = _zip(tmp_path / "dup.zip", [("a/f.csv", b"1"), ("b/f.csv", b"2")])
    with pytest.raises(A.UnsafeArchive, match="same path"):
        A.safe_extract(z, tmp_path / "out", flatten=True)
    assert _files(tmp_path / "out") == []


# --------------------------------------------------------------------------- hostile members


@pytest.mark.parametrize(
    "name, match",
    [
        ("../evil.csv", "traversal"),
        ("a/../../evil.csv", "traversal"),
        ("/etc/evil.csv", "absolute"),
        ("C:/evil.csv", "absolute"),
        ("a\\..\\evil.csv", "backslash"),
    ],
)
def test_zip_slip_rejected(tmp_path, name, match):
    z = _zip(tmp_path / "bad.zip", [("ok.csv", b"ok"), (name, b"x")])
    with pytest.raises(A.UnsafeArchive, match=match):
        A.safe_extract(z, tmp_path / "out")
    assert _files(tmp_path / "out") == []  # validate-all-first: the good member is not written


def test_nul_and_symlink_escape(tmp_path):
    with pytest.raises(A.UnsafeArchive, match="NUL"):
        A._check_name("a\x00b")
    # a pre-existing symlinked dir inside dest that points outside -> realpath escape
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(outside, dest / "lnk")
    z = _zip(tmp_path / "e.zip", [("lnk/x.csv", b"x")])
    with pytest.raises(A.UnsafeArchive, match="escapes"):
        A.safe_extract(z, dest)
    assert list(outside.iterdir()) == []


def test_zip_symlink_member_rejected(tmp_path):
    z = tmp_path / "l.zip"
    with zipfile.ZipFile(z, "w") as zf:
        li = zipfile.ZipInfo("link")
        li.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(li, "/etc/passwd")
    with pytest.raises(A.UnsafeArchive, match="symlink"):
        A.safe_extract(z, tmp_path / "out")


@pytest.mark.parametrize("kind", ["sym", "hard", "dev", "fifo"])
def test_tar_links_and_specials_rejected(tmp_path, kind):
    ti = tarfile.TarInfo("m")
    if kind == "sym":
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
    elif kind == "hard":
        ti.type, ti.linkname = tarfile.LNKTYPE, "ok.csv"
    elif kind == "dev":
        ti.type = tarfile.CHRTYPE
    else:
        ti.type = tarfile.FIFOTYPE
    t = _tar(tmp_path / "t.tar", [("ok.csv", b"ok"), ti])
    with pytest.raises(A.UnsafeArchive, match="refused"):
        A.safe_extract(t, tmp_path / "out")
    assert _files(tmp_path / "out") == []


def test_tar_absolute_and_dotdot(tmp_path):
    for name in ("/abs.csv", "../up.csv"):
        t = _tar(tmp_path / "t.tar", [(name, b"x")])
        with pytest.raises(A.UnsafeArchive):
            A.safe_extract(t, tmp_path / "out")


# --------------------------------------------------------------------------- bomb caps


def test_zip_bomb_ratio(tmp_path):
    z = _zip(tmp_path / "bomb.zip", [("z.bin", b"\0" * 1_000_000)], compression=8)
    with pytest.raises(A.UnsafeArchive, match="zip bomb"):
        A.safe_extract(z, tmp_path / "out", max_ratio=10)
    with pytest.raises(A.UnsafeArchive, match="zip bomb"):
        A.safe_extract(z, tmp_path / "out")  # ~1000:1 also trips the default cap of 200
    assert A.safe_extract(z, tmp_path / "out2", max_ratio=5000)


def test_caps_files_and_total(tmp_path):
    z = _zip(tmp_path / "many.zip", [(f"f{i}.csv", b"12345") for i in range(5)])
    with pytest.raises(A.UnsafeArchive, match="exceeds the cap of 4"):
        A.safe_extract(z, tmp_path / "out", max_files=4)
    with pytest.raises(A.UnsafeArchive, match="declared total size"):
        A.safe_extract(z, tmp_path / "out", max_total_bytes=20)
    assert len(A.safe_extract(z, tmp_path / "out", max_total_bytes=25)) == 5


def test_streaming_size_lie_and_budget(tmp_path):
    class Src(io.BytesIO):
        pass

    with pytest.raises(A.UnsafeArchive, match="declared size"):
        A._copy(Src(b"abcdef"), str(tmp_path / "t1"), name="m", declared=3, budget=100)
    with pytest.raises(A.UnsafeArchive, match="total-size cap"):
        A._copy(Src(b"abcdef"), str(tmp_path / "t2"), name="m", declared=10, budget=3)


def test_interrupted_extract_leaves_no_partial(tmp_path, monkeypatch):
    z = _zip(tmp_path / "s.zip", [("a.csv", b"a" * 10), ("b.csv", b"b" * 10)])
    real = A._copy
    calls = {"n": 0}

    def boom(src, tmp, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            with open(tmp, "wb") as fh:
                fh.write(b"half")
            raise KeyboardInterrupt
        return real(src, tmp, **kw)

    monkeypatch.setattr(A, "_copy", boom)
    dest = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        A.safe_extract(z, dest)
    assert _files(dest) == ["a.csv"]  # first published, second neither final nor tmp

    # a failure before the tmp file exists is cleaned up too
    def early(src, tmp, **kw):
        raise OSError("disk")

    monkeypatch.setattr(A, "_copy", early)
    with pytest.raises(OSError):
        A.safe_extract(z, tmp_path / "out3")
    assert _files(tmp_path / "out3") == []
