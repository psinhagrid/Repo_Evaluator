import zipfile

import pytest

from evaluator.intake.unzip import IntakeRejected, safe_extract


def _zip(path, members: dict[str, bytes], encrypted: bool = False):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    if encrypted:
        # zipfile cannot write encrypted entries; set the encryption bit in both headers.
        raw = bytearray(path.read_bytes())
        for sig, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = raw.find(sig)
            raw[i + flag_offset] |= 0x1
        path.write_bytes(bytes(raw))
    return path


def _code(tmp_path, members, **kw):
    z = _zip(tmp_path / "s.zip", members, **kw)
    with pytest.raises(IntakeRejected) as e:
        safe_extract(z, tmp_path / "out")
    return e.value.code


def test_rejects_path_traversal(tmp_path):
    assert _code(tmp_path, {"../evil.txt": b"x"}) == "path_traversal"
    assert _code(tmp_path, {"/etc/passwd": b"x"}) == "path_traversal"
    assert _code(tmp_path, {"a\\..\\..\\evil": b"x"}) == "path_traversal"


def test_rejects_encrypted(tmp_path):
    assert _code(tmp_path, {"a.txt": b"x"}, encrypted=True) == "encrypted"


def test_rejects_empty_and_not_zip(tmp_path):
    (tmp_path / "empty.zip").write_bytes(b"")
    with pytest.raises(IntakeRejected) as e:
        safe_extract(tmp_path / "empty.zip", tmp_path / "o1")
    assert e.value.code == "empty"
    (tmp_path / "notzip.zip").write_text("hello")
    with pytest.raises(IntakeRejected) as e:
        safe_extract(tmp_path / "notzip.zip", tmp_path / "o2")
    assert e.value.code == "not_a_zip"
    assert _code(tmp_path, {"dir/": b""}) == "empty"


def test_rejects_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr("evaluator.intake.unzip.MAX_UNCOMPRESSED_BYTES", 10)
    assert _code(tmp_path, {"a.txt": b"x" * 11}) == "too_large"


def test_strips_single_root_and_macos_noise(tmp_path):
    z = _zip(tmp_path / "s.zip", {"proj/a.py": b"1", "proj/sub/b.md": b"2", "__MACOSX/proj/._a.py": b"x"})
    r = safe_extract(z, tmp_path / "out")
    assert r.files == ["a.py", "sub/b.md"]
    assert r.stripped_prefix == "proj/"
    assert len(r.archive_sha256) == 64


def test_skips_symlinks(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "ok")
        link = zipfile.ZipInfo("link")
        link.external_attr = (0o120777 << 16)
        zf.writestr(link, "/etc/passwd")
    r = safe_extract(z, tmp_path / "out")
    assert r.files == ["a.txt"]
    assert r.skipped == [{"path": "link", "reason": "symlink"}]
