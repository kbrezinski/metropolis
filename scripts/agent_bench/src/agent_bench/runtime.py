"""File-only authoring workspace; submitted commands are never executed."""

from pathlib import Path, PurePosixPath


FILE_LIMIT = 1024 * 1024
READ_LIMIT = 64 * 1024
TOTAL_LIMIT = 10 * 1024 * 1024
FILE_COUNT_LIMIT = 1024
_DOS_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _relative(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or str(path) == "."
        or ".." in path.parts
        or "\\" in name
        or any(":" in part for part in path.parts)
    ):
        raise ValueError("Artifact paths must be relative POSIX file names without traversal")
    for part in path.parts:
        if (
            part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in _DOS_NAMES
            or any(
                character in '<>:"|?*' or ord(character) < 32 or ord(character) == 127
                for character in part
            )
        ):
            raise ValueError("Artifact path is not a portable regular-file name")
    return path


class Workspace:
    """Expose bounded text-file operations in one fresh attempt directory."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink() or self.path.resolve() != self.path:
            raise ValueError("Workspace directory must not contain links")
        self.path.mkdir(parents=True, exist_ok=False)

    def _target(self, name):
        target = self.path.joinpath(*_relative(name).parts)
        if target.is_symlink() or target.resolve() != target:
            raise ValueError("Artifact links are not accepted")
        return target

    def _files(self):
        files = []
        names = set()
        total = 0
        for path in sorted(self.path.rglob("*")):
            name = path.relative_to(self.path).as_posix()
            self._target(name)
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("Only regular artifact files are accepted")
            if name.casefold() in names:
                raise ValueError("Case-insensitive artifact path collision")
            names.add(name.casefold())
            size = path.stat().st_size
            total += size
            if size > FILE_LIMIT or total > TOTAL_LIMIT or len(files) >= FILE_COUNT_LIMIT:
                raise ValueError("Artifact size/count limit exceeded")
            files.append((name, path))
        return files

    def write_file(self, path, content, timeout=None):
        target = self._target(path)
        data = content.encode("utf-8")
        if len(data) > FILE_LIMIT:
            raise ValueError("File exceeds the 1 MiB write limit")
        files = self._files()
        names = {name.casefold(): name for name, _ in files}
        name = target.relative_to(self.path).as_posix()
        if name.casefold() in names and names[name.casefold()] != name:
            raise ValueError("Case-insensitive artifact path collision")
        total = sum(file.stat().st_size for _, file in files if file != target) + len(data)
        count = len(files) + (not target.exists())
        if total > TOTAL_LIMIT or count > FILE_COUNT_LIMIT:
            raise ValueError("Artifact size/count limit exceeded")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read_file(self, path, timeout=None):
        target = self._target(path)
        with target.open("rb") as stream:
            data = stream.read(READ_LIMIT + 1)
        if len(data) > READ_LIMIT:
            raise ValueError("File exceeds the 64 KiB read limit")
        return data.decode("utf-8")

    def collect(self, destination):
        files = self._files()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        for name, source in files:
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
