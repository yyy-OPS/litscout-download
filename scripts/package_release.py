"""Publish only the standalone download project; never traverse its parent workspace."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.3"
TOP_FILES = {"LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml", ".gitignore", "requirements-runtime.txt"}
TREES = {"litscout_download", "apps", "skills", "scripts", "tests", "docs"}
EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}
SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".toml", ".yaml", ".txt", ".png"}
SECRETS = [re.compile(rb"yfr_cdk_v1_[A-Za-z0-9_-]{20,}"),
           re.compile(rb"(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}"),
           re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
           re.compile(rb"[A-Za-z]:[/\\]Users[/\\](?!Public\b)[^/\\\s\"']+", re.I),
           re.compile(rb"[A-Za-z]:[/\\]yy_work[/\\]", re.I)]


def approved_files():
    if (ROOT / ".git").exists():
        raise RuntimeError("Review repository history separately before packaging a Git checkout")
    entries = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative.parts[0] not in TREES and relative.as_posix() not in TOP_FILES:
            continue
        if path.is_symlink():
            raise RuntimeError("Release may not contain symlinks: " + str(relative))
        if not path.is_file():
            continue
        bundle = relative.as_posix() == "skills/litscout-browser-download/assets/companion.zip"
        if path.name not in TOP_FILES and path.suffix not in SUFFIXES and not bundle:
            raise RuntimeError("Unapproved release type: " + str(relative))
        if path.name.startswith(".env") or path.name == "agent-token" or path.suffix in {".pdf", ".db", ".sqlite3"}:
            raise RuntimeError("Private file in release tree: " + str(relative))
        content = path.read_bytes()
        if path.suffix != ".png" and not bundle:
            if any(pattern.search(content) for pattern in SECRETS):
                raise RuntimeError("Potential secret or personal path: " + str(relative))
            if relative.parts[0] == "litscout_download" and (b"litscout-literature-cloud-source" in content or b"class Cloud:" in content):
                raise RuntimeError("Cloud implementation reference in public code: " + str(relative))
        entries.append((path, relative.as_posix()))
    return entries


def archive(output, name, entries):
    target = output / name
    with ZipFile(target, "w", ZIP_DEFLATED) as package:
        for path, relative in entries:
            package.write(path, relative)
    with ZipFile(target) as package:
        if package.testzip() is not None:
            raise RuntimeError("ZIP integrity failed")
    return {"name": name, "files": len(entries), "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / VERSION)
    args = parser.parse_args()
    output = args.output.resolve()
    names = [f"litscout-download-{VERSION}-source.zip", f"litscout-download-{VERSION}-extension.zip", f"litscout-browser-download-{VERSION}-skill.zip"]
    if any((output / name).exists() for name in names + ["SHA256SUMS.txt", "release-manifest.json"]):
        raise SystemExit("Choose a fresh output directory; release artifacts are not overwritten")
    # A standalone Skill contains the public companion and extension, not user data.
    sys.path.insert(0, str(ROOT / "skills/litscout-browser-download/scripts"))
    from setup import source_files
    assets = ROOT / "skills/litscout-browser-download/assets"
    assets.mkdir(exist_ok=True)
    bundle = assets / "companion.zip"
    with ZipFile(bundle, "w", ZIP_DEFLATED) as package:
        for path in source_files(ROOT):
            package.write(path, path.relative_to(ROOT).as_posix())
    (assets / "companion.json").write_text(json.dumps({"sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "version": VERSION}), encoding="utf-8")
    entries = approved_files()
    extension = [(path, relative.removeprefix("apps/browser-extension/")) for path, relative in entries
                 if relative.startswith("apps/browser-extension/") and "/tests/" not in relative and not relative.endswith("package.json")]
    extension.append((ROOT / "LICENSE", "LICENSE"))
    skill = [(path, relative.removeprefix("skills/")) for path, relative in entries if relative.startswith("skills/")]
    source = [(path, f"litscout-download-{VERSION}/" + relative) for path, relative in entries]
    output.mkdir(parents=True, exist_ok=True)
    result = [archive(output, name, files) for name, files in zip(names, [source, extension, skill])]
    (output / "SHA256SUMS.txt").write_text("".join(f"{value['sha256']}  {value['name']}\n" for value in result), encoding="utf-8")
    (output / "release-manifest.json").write_text(json.dumps({"version": VERSION, "license": "MIT",
        "historyIncluded": False, "userDataIncluded": False, "files": result,
        "sourceFiles": [relative for _, relative in entries]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "artifacts": result}, indent=2))


if __name__ == "__main__":
    main()
