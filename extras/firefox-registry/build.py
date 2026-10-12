"""Package the unbundled extension for Mozilla signing; does not upload it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile


FILES = ("manifest.json", "entry.js", "background.js", "options.html", "options.js", "options.css", "icon.svg")


def main() -> None:
    source = Path(__file__).resolve().parent
    version = json.loads((source / "manifest.json").read_text())["version"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=source.parents[1] / ".tmp/firefox-registry" / f"project-registry-{version}.zip")
    output = parser.parse_args().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(source / name, name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise RuntimeError("extension archive is corrupt")
    print(output)


if __name__ == "__main__":
    main()
