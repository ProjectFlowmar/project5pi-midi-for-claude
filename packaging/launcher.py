"""Entry point for the packaged Windows app: start the Studio and open the browser."""
import sys

from p5midi.studio import main

if __name__ == '__main__':
    main(open_browser='--no-browser' not in sys.argv)
