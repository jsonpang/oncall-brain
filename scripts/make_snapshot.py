"""Export HEAD into a new local repository; never contact a remote."""

import argparse
import ast
import fnmatch
import io
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit


EXCLUDE_PATTERNS = [
    "eval/*.log", "eval/**/*.log",
    ".env*", "*.local.*",
    "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", "cache", ".venv", "venv", "node_modules",
    "*.tmp", "*.temp", "*.swp", "*.swo", "*~", ".DS_Store", "Thumbs.db",
    "corpus/scan.py",  # The legacy scanner embeds the private scan expression.
]


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True
    ).stdout


def excluded(name):
    parts = PurePosixPath(name).parts
    for pattern in EXCLUDE_PATTERNS:
        if "/" in pattern:
            if fnmatch.fnmatchcase(name, pattern):
                return True
        elif any(
            fnmatch.fnmatchcase(part, pattern)
            and not (pattern == ".env*" and part == ".env.example")
            for part in parts
        ):
            return True
    return False


def scan_pattern(filename):
    """Read the literal pat = re.compile(...) without executing external code."""
    tree = ast.parse(Path(filename).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pat" for t in node.targets):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "re" and call.func.attr == "compile"):
            raise ValueError("pat must be a literal re.compile expression")

        def flags(value):
            if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                if value.value.id == "re" and isinstance(getattr(re, value.attr, None), re.RegexFlag):
                    return getattr(re, value.attr)
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                return flags(value.left) | flags(value.right)
            return ast.literal_eval(value)

        options = flags(call.args[1]) if len(call.args) > 1 else 0
        for kw in call.keywords:
            if kw.arg != "flags":
                raise ValueError("Unsupported compile keyword")
            options = flags(kw.value)
        return re.compile(ast.literal_eval(call.args[0]), options)
    raise ValueError("No literal pat assignment found")


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.I)


def ip_url_matches(line):
    for match in URL_PATTERN.finditer(line):
        try:
            host = urlsplit(match.group()).hostname
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if str(address) != "127.0.0.1":
            yield match


def safe_word(word):
    """Keep diagnostic words, but never expose address or key matches."""
    if re.search(r"\d+\.\d+\.", word) or "://" in word:
        return "<IP>"
    if word.lower().startswith("sk-") or "PRIVATE KEY" in word.upper():
        return "<KEY>"
    return word


def scan(target, pattern):
    hits = []
    for path in sorted(target.rglob("*")):
        relative = path.relative_to(target)
        if ".git" in relative.parts or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Do not silently skip non-UTF-8 text containing ASCII secrets.
            content = data.decode("latin-1")
        for number, line in enumerate(content.splitlines(), 1):
            matches = list(pattern.finditer(line)) + list(ip_url_matches(line))
            for word in sorted({safe_word(m.group()) for m in matches}):
                hit = (relative.as_posix(), number, word)
                hits.append(hit)
                print("{}:{}:{}".format(*hit))
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default="../oncall-public")
    args = parser.parse_args()
    source = Path(git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
                  .decode().strip()).resolve()
    requested = Path(args.target).absolute()
    if requested.is_symlink() or requested.is_junction():
        raise ValueError("Refusing a linked target directory")
    target = requested.resolve()
    protected = [source, Path(git(source, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()]
    for path in protected:
        if target == path or target in path.parents or path in target.parents:
            raise ValueError("Target must be disjoint from the source repository")
    if target.exists() and not target.is_dir():
        raise ValueError("Target must be a directory")

    # Validate identity, rule and archive before removing an existing destination.
    identity = {key: git(source, "config", "--get", key).decode().strip()
                for key in ("user.name", "user.email")}
    if not all(identity.values()):
        raise ValueError("Source git author configuration is missing")
    rule_file = os.environ.get("SNAPSHOT_SCAN_FILE")
    pattern = scan_pattern(rule_file) if rule_file else None
    archive = git(source, "archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name:
                raise ValueError("Unsafe archive path")
            if not (member.isdir() or member.isfile()):
                raise ValueError("Unsupported archive entry type")
        print("Target validated as disjoint from source; preparing snapshot.")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        skipped = []
        for member in members:
            if not member.isfile():
                continue
            if excluded(member.name):
                skipped.append(member.name)
                continue
            dest = target / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(member).read())
            dest.chmod(member.mode & 0o777)

    # This new tool is intentionally uncommitted in the source repository.
    tool = target / "scripts" / "make_snapshot.py"
    tool.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve(), tool)
    print("Exported files:", sum(p.is_file() for p in target.rglob("*")))
    print("Excluded files:", len(skipped))
    for name in skipped:
        print("Excluded:", name)
    if pattern is None:
        print("WARNING: SNAPSHOT_SCAN_FILE unset; sensitive scanning skipped.", file=sys.stderr)
        hits = []
    else:
        hits = scan(target, pattern)
        print("Scan findings:", len(hits))

    git(target, "init")
    for key, value in identity.items():
        git(target, "config", "--local", key, value)
    git(target, "add", "--all", "--force")
    # Override ambient author variables and signing inherited from global config.
    env = os.environ.copy()
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = identity["user.name"]
        env[f"GIT_{role}_EMAIL"] = identity["user.email"]
    subprocess.run(["git", "-C", str(target), "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=", "commit", "-m", "Initial public snapshot"],
                   env=env, check=True, capture_output=True)
    print("Created one local initial commit.")
    return 1 if hits else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError, tarfile.TarError):
        print("ERROR: Snapshot failed; check target, Git configuration and external rule file.", file=sys.stderr)
        sys.exit(2)
