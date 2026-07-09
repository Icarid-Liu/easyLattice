from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "deploy" / "huggingface-live"


def main() -> None:
    args = parse_args()
    token = args.token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise SystemExit("Set HF_TOKEN or pass --token with a Hugging Face write token.")

    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit("Install huggingface_hub first: python3 -m pip install huggingface_hub") from exc

    with tempfile.TemporaryDirectory(prefix="easylattice-hf-live-") as tmpdir:
        context = Path(tmpdir)
        build_context(context)
        api = HfApi(token=token)
        api.create_repo(
            repo_id=args.repo_id,
            repo_type="space",
            space_sdk="docker",
            private=not args.public,
            exist_ok=True,
        )
        api.upload_folder(
            repo_id=args.repo_id,
            repo_type="space",
            folder_path=str(context),
            commit_message=args.message,
        )

    print(f"Uploaded Hugging Face Space: https://huggingface.co/spaces/{args.repo_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy the easyLattice live API to a Hugging Face Docker Space.")
    parser.add_argument("--repo-id", required=True, help="Space repo id, for example USER/easyLattice-live.")
    parser.add_argument("--token", help="Hugging Face write token. Defaults to HF_TOKEN.")
    parser.add_argument("--public", action="store_true", help="Create or keep the Space public.")
    parser.add_argument(
        "--message",
        default="Deploy easyLattice live API",
        help="Commit message for the Space upload.",
    )
    return parser.parse_args()


def build_context(context: Path) -> None:
    copy_tree(ROOT / "app", context / "app")
    copy_tree(ROOT / "static", context / "static")
    shutil.copy2(ROOT / "LICENSE", context / "LICENSE")
    shutil.copy2(ROOT / "config.local.example.json", context / "config.local.example.json")
    shutil.copy2(TEMPLATE_ROOT / "Dockerfile", context / "Dockerfile")
    shutil.copy2(TEMPLATE_ROOT / "SpaceREADME.md", context / "README.md")


def copy_tree(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    shutil.copytree(source, destination, ignore=ignore)


if __name__ == "__main__":
    main()
