"""Build a Python 3.12 Linux x86_64 Lambda zip, including native Linux wheels."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=root / "dist" / "securedrop-api.zip")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="securedrop-lambda-") as directory:
        work = Path(directory)
        requirements = work / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        target = work / "package"
        subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pip",
                "python",
                "-m",
                "pip",
                "install",
                "--platform",
                "manylinux2014_x86_64",
                "--implementation",
                "cp",
                "--python-version",
                "3.12",
                "--only-binary=:all:",
                "--no-compile",
                "--target",
                str(target),
                "-r",
                str(requirements),
            ],
            cwd=root,
            check=True,
        )
        for source in root.glob("*.py"):
            shutil.copy2(source, target / source.name)
        with ZipFile(args.output, "w", ZIP_DEFLATED) as archive:
            for source in target.rglob("*"):
                if source.is_file() and "__pycache__" not in source.parts:
                    archive.write(source, source.relative_to(target))
    print(f"Lambda archive: {args.output}")


if __name__ == "__main__":
    main()
