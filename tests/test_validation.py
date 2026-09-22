"""Test our compatibility policy against parsed metadata supplied by the readers."""

from types import SimpleNamespace as Record
from unittest.mock import MagicMock

import pytest
from elftools.elf.dynamic import DynamicSegment
from elftools.elf.gnuversions import GNUVerNeedSection
from macholib import mach_o
from macholib.ptypes import sizeof

from tools.elf import audit_elf
from tools.macho import audit_macho


@pytest.fixture
def elf(tmp_path, monkeypatch):
    path = tmp_path / "binary"
    path.touch()
    segment = MagicMock(spec=DynamicSegment)
    segment.iter_tags.return_value = []
    versions = MagicMock(spec=GNUVerNeedSection)
    versions.iter_versions.return_value = [(None, [Record(name="GLIBC_2.17")])]
    reader = MagicMock(elfclass=64, little_endian=True)
    reader.__getitem__.return_value = "EM_X86_64"
    reader.iter_segments.return_value = [segment]
    reader.get_section_by_name.return_value = versions
    monkeypatch.setattr("tools.elf.ELFFile", lambda _: reader)
    return Record(path=path, reader=reader, segment=segment, versions=versions)


def test_elf_glibc_boundary_and_compiler_runtime(elf):
    audit_elf(elf.path, "x86_64")
    for version in ("GLIBC_2.18", "GLIBCXX_3.4.20"):
        elf.versions.iter_versions.return_value = [(None, [Record(name=version)])]
        with pytest.raises(ValueError, match="glibc|compiler runtime"):
            audit_elf(elf.path, "x86_64")


def test_elf_library_policy(elf):
    needed = Record(entry=Record(d_tag="DT_NEEDED"), needed="libc.so.6")
    elf.segment.iter_tags.return_value = [needed]
    audit_elf(elf.path, "x86_64")
    needed.needed = "libatomic.so.1"
    with pytest.raises(ValueError, match="shared libraries"):
        audit_elf(elf.path, "x86_64")


@pytest.mark.parametrize("tag", ["DT_RPATH", "DT_RUNPATH"])
def test_elf_rejects_search_paths(elf, tag):
    elf.segment.iter_tags.return_value = [Record(entry=Record(d_tag=tag))]
    with pytest.raises(ValueError, match="search path"):
        audit_elf(elf.path, "x86_64")


def test_elf_missing_version_sections_cannot_bypass_audit(elf):
    elf.segment.iter_tags.return_value = [Record(entry=Record(d_tag="DT_VERNEED"))]
    elf.reader.get_section_by_name.return_value = None
    with pytest.raises(ValueError, match="symbol-version"):
        audit_elf(elf.path, "x86_64")


def test_elf_rejects_wrong_architecture(elf):
    with pytest.raises(ValueError, match="architecture"):
        audit_elf(elf.path, "aarch64")


@pytest.fixture
def macho(tmp_path, monkeypatch):
    header = Record(header=Record(cputype=0x01000007, cpusubtype=3), commands=[])
    monkeypatch.setattr("tools.macho.MachO", lambda _: Record(headers=[header]))
    return tmp_path / "binary", header


@pytest.mark.parametrize("legacy", [True, False])
def test_macho_deployment_boundary(macho, legacy):
    path, header = macho
    for version in (0x0C0300, 0x0C0400):
        header.commands = [
            (Record(cmd=mach_o.LC_VERSION_MIN_MACOSX), Record(version=version), b"")
            if legacy
            else (Record(cmd=mach_o.LC_BUILD_VERSION), Record(minos=version, platform=1), b"")
        ]
        if version == 0x0C0300:
            audit_macho(path, "12.3", "x86_64")
        else:
            with pytest.raises(ValueError, match="deployment target"):
                audit_macho(path, "12.3", "x86_64")


def test_macho_rejects_non_system_library(macho):
    path, header = macho
    load = mach_o.load_command(cmd=mach_o.LC_LOAD_DYLIB)
    command = mach_o.dylib_command(name=sizeof(load) + sizeof(mach_o.dylib_command))
    header.commands = [(load, command, b"/opt/homebrew/lib/libfoo.dylib\0")]
    with pytest.raises(ValueError, match="Non-system"):
        audit_macho(path, "12.3", "x86_64")


def test_macho_rejects_search_paths_and_missing_deployment_target(macho):
    path, header = macho
    with pytest.raises(ValueError, match="deployment target"):
        audit_macho(path, "12.3", "x86_64")
    header.commands = [(Record(cmd=mach_o.LC_RPATH), None, b"")]
    with pytest.raises(ValueError, match="search path"):
        audit_macho(path, "12.3", "x86_64")


def test_macho_rejects_specialized_architecture(macho):
    path, header = macho
    header.header.cpusubtype = 8  # x86_64h requires Haswell.
    with pytest.raises(ValueError, match="architecture subtype"):
        audit_macho(path, "12.3", "x86_64")
