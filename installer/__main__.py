import os
import sys

from . import installer, utils
from .app import App
from .config import Config


def main():
	if os.geteuid() != 0:
		sys.exit("Must be run as root")
	fake_mode = "--test" in sys.argv[1:]
	config = Config(boot_mode=utils.detect_boot_mode(), fake_mode=fake_mode)
	if App(config).run() and not config.fake_mode:
		installer.run_install(config)


if __name__ == "__main__":
	main()
