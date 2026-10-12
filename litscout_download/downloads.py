"""Read only bounded PDF files within the user's configured Downloads directory."""
import os
from pathlib import Path

from .store import Problem


def default_download_dir():
    if os.environ.get("LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR"):
        return Path(os.environ["LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR"])
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                return Path(os.path.expandvars(value))
        except OSError:
            pass
    return Path.home() / "Downloads"


class Downloads:
    def __init__(self, root=None):
        self.root = Path(root or default_download_dir()).resolve()
        if self.root == Path.home().resolve() or self.root == Path(self.root.anchor) or str(self.root).startswith("\\\\"):
            raise ValueError("Download directory must be a dedicated local directory")

    def path(self, filename):
        candidate = Path(filename)
        if not candidate.is_absolute() or candidate.suffix.lower() != ".pdf":
            raise Problem("SAVED_FILE_OUTSIDE_DOWNLOADS", 422)
        try:
            path = candidate.resolve(strict=True)
        except OSError:
            raise Problem("SAVED_FILE_MISSING", 422) from None
        if not path.is_relative_to(self.root) or not path.is_file() or candidate.is_symlink():
            raise Problem("SAVED_FILE_OUTSIDE_DOWNLOADS", 422)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise Problem("PDF_TOO_LARGE", 413)
        return path

    async def chunks(self, filename):
        path = self.path(filename)
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                yield chunk
