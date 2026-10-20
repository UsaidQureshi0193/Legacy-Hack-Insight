"""Create/update the Hugging Face Space with the dashboard code.

    set HF_TOKEN=hf_...            (a write token, kept outside the repo)
    python scripts/deploy_space.py your-team/hackinsight-dashboard your-team/hackinsight-live

Arg 1 = Space repo, arg 2 = dataset repo the hourly flow publishes to (becomes HI_HF_DATASET).
"""
from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]


def main(space: str, dataset: str) -> None:
    api = HfApi()
    api.create_repo(space, repo_type="space", space_sdk="gradio", exist_ok=True)
    api.add_space_variable(space, "HI_HF_DATASET", dataset)
    for f in ("app.py", "render.py", "README.md", "requirements.txt"):
        api.upload_file(path_or_fileobj=str(ROOT / "app" / f), path_in_repo=f, repo_id=space, repo_type="space")
    api.upload_folder(folder_path=str(ROOT / "src" / "hackinsight"), path_in_repo="src/hackinsight",
                      repo_id=space, repo_type="space", ignore_patterns=["__pycache__/*", "*.pyc"])
    print(f"https://huggingface.co/spaces/{space}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
