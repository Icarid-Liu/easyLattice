from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "easyLattice-runner.pyz"
EXTRACTOR = r'''from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    archive = Path(sys.argv[0]).resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()[:16]
    cache_root = Path(os.environ.get("EASYLATTICE_RUNNER_CACHE", Path.home() / ".cache" / "easyLattice-runner"))
    destination = cache_root / digest
    marker = destination / ".ready"
    if not marker.is_file():
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f"{digest}-", dir=cache_root))
        try:
            root = temporary.resolve()
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    target = (temporary / member.filename).resolve()
                    if target != root and root not in target.parents:
                        raise RuntimeError("runner archive contains an unsafe path")
                    bundle.extract(member, temporary)
            (temporary / ".ready").write_text("ok\n", encoding="utf-8")
            try:
                os.replace(temporary, destination)
            except FileExistsError:
                shutil.rmtree(temporary, ignore_errors=True)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    os.chdir(destination)
    os.execv(sys.executable, [sys.executable, "-m", "app.local_runner", *sys.argv[1:]])


if __name__ == "__main__":
    main()
'''


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="easyLattice-runner-build-") as temporary:
        staging = Path(temporary)
        copy_tree(ROOT / "app", staging / "app")
        copy_tree(ROOT / "static", staging / "static")
        (staging / "__main__.py").write_text(EXTRACTOR, encoding="utf-8")
        zipapp.create_archive(
            staging,
            target=OUTPUT,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    print(OUTPUT)


if __name__ == "__main__":
    main()
