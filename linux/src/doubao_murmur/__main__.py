"""Entry point for Doubao Murmur Linux."""

import logging
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Must run before GTK/WebKit are imported: both read the renderer
    # choice from the environment at startup.
    from doubao_murmur import gpu_quirks

    gpu_quirks.apply()

    from doubao_murmur.app import DoubaoMurmurApp

    app = DoubaoMurmurApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
