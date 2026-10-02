"""Folders listed through a handle held open, never by opening their path a second time (#230).

A folder checked by path and listed later by that path can be swapped in between for a symlink or Windows junction,
by a process an agent left running, and the listing then follows it. Here a folder is opened once without following
a link, checked through that handle and listed through it; each subfolder is opened relative to its parent's handle
the same way. On Windows the handle does not share delete access: while it is open the folder cannot be renamed,
removed or replaced, and neither can a folder above it.
"""

from __future__ import annotations

import errno
import os
import stat
import struct
from pathlib import Path
from typing import NamedTuple


class HeldEntry(NamedTuple):
    name: str
    kind: str | None  # "dir", "file" (regular), "link" (symlink, junction or other reparse point), "other"; None: gone
    ident: tuple[int, int] | None  # (device, inode) as POSIX saw it through the held folder; None on Windows


class NotPlainFolder(OSError):
    """The name is a link or not a folder, or no longer the folder that was listed under it."""


# O_NOFOLLOW on a link fails with ELOOP (EMLINK on FreeBSD), O_DIRECTORY on a file with ENOTDIR.
_LINK_ERRNOS = {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", errno.ELOOP)}
_ERROR_DIRECTORY = 267  # Windows: a file where a folder was asked for
_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
          | getattr(os, "O_CLOEXEC", 0))
_FILE_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
               | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0))


def _not_plain(exc: OSError) -> OSError:
    if exc.errno in _LINK_ERRNOS or getattr(exc, "winerror", None) == _ERROR_DIRECTORY:
        return NotPlainFolder(exc.errno, f"a link or not a folder: {exc.strerror}")
    return exc


class HeldDir:
    """One folder held open. `dev` is its device on POSIX, for telling a mount below it apart; None on Windows,
    where a mount point is a reparse point and is never opened as a folder."""

    def __init__(self, handle: int, ident: tuple[int, int], dev: int | None, path: Path | None = None):
        self._handle: int | None = handle
        self.ident, self.dev, self.path = ident, dev, path

    @classmethod
    def hold(cls, path: Path) -> HeldDir:
        """`path` itself, not followed if it is a link. NotPlainFolder when it is a link or not a folder."""
        try:
            handle = _win_open(str(path)) if os.name == "nt" else os.open(path, _FLAGS)
        except OSError as exc:
            raise _not_plain(exc) from None
        return cls._checked(handle, Path(path))

    def child(self, name: str, expect: tuple[int, int] | None = None) -> HeldDir:
        """Subfolder `name`, opened relative to this folder without following a link. NotPlainFolder when it is a
        link or not a folder now, or is not the folder `expect` identifies (it was replaced after the listing)."""
        try:
            handle = (_win_open_child(self._handle, name) if os.name == "nt"
                      else os.open(name, _FLAGS, dir_fd=self._handle))
        except OSError as exc:
            raise _not_plain(exc) from None
        held = self._checked(handle, self.path / name if self.path is not None else None)
        if expect is not None and held.ident != expect:
            held.close()
            raise NotPlainFolder(errno.ESTALE, f"{name} was replaced after it was listed")
        return held

    def same_as(self, path: Path) -> bool:
        """Whether `path`, its last part not followed, names this folder."""
        try:
            other = HeldDir.hold(path)
        except OSError:
            return False
        with other:
            return other.ident == self.ident

    def open_read_file(self, name: str) -> int:
        """Open a regular child file for reading relative to this held folder, without following a link."""
        if os.name == "nt":
            handle = _win_open_child_file(self._handle, name)
            try:
                attributes, _ident = _win_info(handle)
                if (attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        or attributes & stat.FILE_ATTRIBUTE_DIRECTORY):
                    raise NotPlainFolder(errno.ENOTDIR, f"{name} is a link or not a regular file")
                fd = msvcrt.open_osfhandle(handle, _FILE_FLAGS)
                handle = None
                return fd
            finally:
                if handle is not None:
                    _close(handle)
        try:
            fd = os.open(name, _FILE_FLAGS, dir_fd=self._handle)
        except OSError as exc:
            raise _not_plain(exc) from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise NotPlainFolder(errno.ENOTDIR, f"{name} is not a regular file")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def entries(self, limit: int) -> list[HeldEntry]:
        """At most `limit` entries of this folder, in the order the file system lists them."""
        if os.name == "nt":
            return _win_entries(self._handle, limit)
        found: list[HeldEntry] = []
        with os.scandir(self._handle) as iterator:  # a descriptor: the path is never looked up again
            for entry in iterator:
                if len(found) >= limit:
                    break
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    found.append(HeldEntry(entry.name, None, None))
                    continue
                mode = info.st_mode
                kind = ("link" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode)
                        else "file" if stat.S_ISREG(mode) else "other")
                found.append(HeldEntry(entry.name, kind, (info.st_dev, info.st_ino)))
        return found

    def open_file(self, name: str, flags: int, mode: int = 0o666) -> int:
        """Open a direct child without following a link or Windows reparse point."""
        if self._handle is None:
            raise OSError(errno.EBADF, "folder handle is closed")
        if os.name == "nt":
            if self.path is None:
                raise OSError(errno.EBADF, "held folder has no stable path")
            return _win_open_file(str(self.path / name), flags)
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=self._handle)

    def replace(self, source: str, target: str) -> None:
        if self._handle is None:
            raise OSError(errno.EBADF, "folder handle is closed")
        if os.name == "nt":
            if self.path is None:
                raise OSError(errno.EBADF, "held folder has no stable path")
            os.replace(self.path / source, self.path / target)
        else:
            os.replace(source, target, src_dir_fd=self._handle, dst_dir_fd=self._handle)

    def fsync(self) -> None:
        if os.name != "nt" and self._handle is not None:
            os.fsync(self._handle)

    def chmod(self, mode: int) -> None:
        if self._handle is None or os.name == "nt":
            raise OSError(errno.EBADF, "POSIX folder handle required")
        os.fchmod(self._handle, mode)

    def chown(self, uid: int, gid: int) -> None:
        if self._handle is None or os.name == "nt":
            raise OSError(errno.EBADF, "POSIX folder handle required")
        os.fchown(self._handle, uid, gid)

    def unlink(self, name: str) -> None:
        if self._handle is None:
            raise OSError(errno.EBADF, "folder handle is closed")
        if os.name == "nt":
            if self.path is None:
                raise OSError(errno.EBADF, "held folder has no stable path")
            (self.path / name).unlink(missing_ok=True)
        else:
            try:
                os.unlink(name, dir_fd=self._handle)
            except FileNotFoundError:
                pass

    @staticmethod
    def _checked(handle: int, path: Path | None = None) -> HeldDir:
        try:
            if os.name == "nt":
                attributes, ident = _win_info(handle)
                if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or not attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    raise NotPlainFolder(errno.ENOTDIR, "a link or not a folder")
                return HeldDir(handle, ident, None, path)
            info = os.fstat(handle)
            if not stat.S_ISDIR(info.st_mode):
                raise NotPlainFolder(errno.ENOTDIR, "not a folder")
            return HeldDir(handle, (info.st_dev, info.st_ino), info.st_dev, path)
        except BaseException:
            _close(handle)
            raise

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            _close(handle)

    def __enter__(self) -> HeldDir:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _close(handle: int) -> None:
    if os.name == "nt":
        _kernel32.CloseHandle(handle)
    else:
        os.close(handle)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _ACCESS = 0x0001 | 0x0080 | 0x00100000  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE
    _SHARE = 0x1 | 0x2  # read and write, never delete: the folder stays where it is while it is held
    _INVALID = wintypes.HANDLE(-1).value
    _NO_MORE_FILES = 18
    _FULL_DIRECTORY, _FULL_DIRECTORY_RESTART = 14, 15  # FILE_INFO_BY_HANDLE_CLASS

    class _HandleInfo(ctypes.Structure):  # BY_HANDLE_FILE_INFORMATION
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME), ("accessed", wintypes.FILETIME),
                    ("written", wintypes.FILETIME), ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD), ("index_high", wintypes.DWORD),
                    ("index_low", wintypes.DWORD)]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(_UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HandleInfo)]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                       wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                                    ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatusBlock),
                                    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                                    ctypes.c_void_p, wintypes.ULONG]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]

    def _win_open(path: str) -> int:
        # FILE_FLAG_BACKUP_SEMANTICS opens a folder; FILE_FLAG_OPEN_REPARSE_POINT opens a junction itself.
        handle = _kernel32.CreateFileW(path, _ACCESS, _SHARE, None, 3, 0x02000000 | 0x00200000, None)
        if handle in (None, _INVALID):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def _win_open_child(parent: int, name: str) -> int:
        """NtCreateFile relative to the parent's handle: Windows' openat."""
        buffer = ctypes.create_unicode_buffer(name)
        size = ctypes.sizeof(buffer) - ctypes.sizeof(ctypes.c_wchar)
        object_name = _UnicodeString(size, size, ctypes.cast(buffer, ctypes.c_void_p))
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(object_name), 0,
                                       None, None)
        handle, status_block = wintypes.HANDLE(), _IoStatusBlock()
        # FILE_OPEN; FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT
        status = _ntdll.NtCreateFile(ctypes.byref(handle), _ACCESS, ctypes.byref(attributes),
                                     ctypes.byref(status_block), None, 0, _SHARE, 1, 0x1 | 0x20 | 0x200000, None, 0)
        if status < 0:
            raise ctypes.WinError(_ntdll.RtlNtStatusToDosError(status))
        return handle.value

    def _win_open_child_file(parent: int, name: str) -> int:
        """NtCreateFile relative to the parent's handle, requiring a non-directory non-link file."""
        buffer = ctypes.create_unicode_buffer(name)
        size = ctypes.sizeof(buffer) - ctypes.sizeof(ctypes.c_wchar)
        object_name = _UnicodeString(size, size, ctypes.cast(buffer, ctypes.c_void_p))
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(object_name), 0,
                                       None, None)
        handle, status_block = wintypes.HANDLE(), _IoStatusBlock()
        # FILE_OPEN; FILE_NON_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT
        status = _ntdll.NtCreateFile(ctypes.byref(handle), _ACCESS, ctypes.byref(attributes),
                                     ctypes.byref(status_block), None, 0, _SHARE, 1, 0x40 | 0x20 | 0x200000,
                                     None, 0)
        if status < 0:
            raise ctypes.WinError(_ntdll.RtlNtStatusToDosError(status))
        return handle.value

    def _win_open_file(path: str, flags: int) -> int:
        access = 0x40000000  # GENERIC_WRITE
        if flags & os.O_APPEND:
            access |= 0x00000004  # FILE_APPEND_DATA
        if flags & os.O_CREAT and flags & os.O_EXCL:
            disposition = 1  # CREATE_NEW
        elif flags & os.O_CREAT and flags & os.O_TRUNC:
            disposition = 2  # CREATE_ALWAYS
        elif flags & os.O_CREAT:
            disposition = 4  # OPEN_ALWAYS
        elif flags & os.O_TRUNC:
            disposition = 5  # TRUNCATE_EXISTING
        else:
            disposition = 3  # OPEN_EXISTING
        handle = _kernel32.CreateFileW(path, access, _SHARE, None, disposition,
                                       0x00200000, None)  # FILE_FLAG_OPEN_REPARSE_POINT
        if handle in (None, _INVALID):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes, _ident = _win_info(handle)
            if attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError(errno.ELOOP, "a link or folder occupies the file slot")
            return msvcrt.open_osfhandle(handle, flags | getattr(os, "O_BINARY", 0))
        except BaseException:
            _kernel32.CloseHandle(handle)
            raise

    def _win_info(handle: int) -> tuple[int, tuple[int, int]]:
        info = _HandleInfo()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.attributes, (info.volume, (info.index_high << 32) | info.index_low)

    def _win_entries(handle: int, limit: int) -> list[HeldEntry]:
        found: list[HeldEntry] = []
        buffer = ctypes.create_string_buffer(64 * 1024)
        info_class = _FULL_DIRECTORY_RESTART
        while len(found) < limit:
            if not _kernel32.GetFileInformationByHandleEx(handle, info_class, buffer, ctypes.sizeof(buffer)):
                code = ctypes.get_last_error()
                if code == _NO_MORE_FILES:
                    break
                raise ctypes.WinError(code)
            info_class = _FULL_DIRECTORY
            offset = 0
            while len(found) < limit:  # FILE_FULL_DIR_INFO records, each NextEntryOffset bytes after the last
                step, = struct.unpack_from("<I", buffer, offset)
                attributes, name_bytes = struct.unpack_from("<II", buffer, offset + 56)
                name = ctypes.wstring_at(ctypes.addressof(buffer) + offset + 68, name_bytes // 2)
                if name not in (".", ".."):
                    kind = ("link" if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                            else "dir" if attributes & stat.FILE_ATTRIBUTE_DIRECTORY else "file")
                    found.append(HeldEntry(name, kind, None))
                if not step:
                    break
                offset += step
        return found
