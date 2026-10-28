"""`python -m saapp_runner ...` does what the `saapp-runner` command does. The entry that starts the runner at login uses this form, so it needs no command shim."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
