"""Read a purpose-specific key from the user's terminal, never from other apps."""

import getpass
import os
import sys
from pathlib import Path


def main():
    if not sys.stdin.isatty():
        raise SystemExit(
            "Run this helper yourself in a local interactive terminal. Do not pipe or paste a credential into chat."
        )
    directory = Path(".prism-demo")
    marker = directory / "synthetic-demo-v1"
    if directory.is_symlink() or not marker.is_file() or marker.is_symlink():
        raise SystemExit(
            "First start the synthetic demo once from the repository root, then stop it."
        )
    target = directory / "prism-openai.key"
    if target.exists() or target.is_symlink():
        raise SystemExit(
            "The Prism key file already exists. Edit or rotate it yourself; this helper will not overwrite it."
        )
    print(
        "Create a purpose-specific OpenAI API credential in a project you authorize for Prism."
    )
    print(
        "The key stays in the ignored local demo directory. This helper makes no API call."
    )
    key = getpass.getpass("Prism OpenAI API key (input hidden): ").strip()
    if (
        not key.startswith("sk-")
        or not 20 <= len(key) <= 4096
        or any(c.isspace() for c in key)
    ):
        raise SystemExit("Unexpected credential format; no file was written.")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(key + "\n")
    print(
        "Credential saved locally with owner-only permissions. No model request was sent."
    )
    print("To explicitly authorize the documented route and allowance, start:")
    print(
        "uv run --no-editable prism demo --allow-openai --openai-key-file .prism-demo/prism-openai.key"
    )


if __name__ == "__main__":
    main()
