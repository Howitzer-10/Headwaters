"""
setup_models.py — Dev CLI wrapper: provision the local Ollama models for the proxy.

Equivalent to the tray app's "Download Local Models" action. Run:

    python setup_models.py
"""

import sys

import model_provisioner


def main() -> int:
    if not model_provisioner.ollama_available():
        print("ERROR: 'ollama' command not found. Install Ollama from https://ollama.com/")
        return 1

    successes, failures = model_provisioner.provision_models(progress=print)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
