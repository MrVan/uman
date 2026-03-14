#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
# Copyright 2025 Canonical Ltd
# Written by Simon Glass <simon.glass@canonical.com>

"""U-Boot Manager (uman) - automates U-Boot development tasks"""

import os
import sys

# Use the embedded u_boot_pylib by putting uman's parent first on
# sys.path, so it takes priority over any older version in the U-Boot
# tree's tools/ directory.  Set UMAN_EXTERNAL_PYLIB=1 to use the
# UBOOT_TOOLS version instead (for testing newer versions).
our_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.dirname(our_path)
if os.environ.get('UMAN_EXTERNAL_PYLIB'):
    uboot_tools = os.path.expanduser(
        os.environ.get('UBOOT_TOOLS', '~/u/tools'))
    sys.path.insert(0, uboot_tools)
else:
    sys.path.insert(0, parent_path)

# pylint: disable=import-error,wrong-import-position
from uman_pkg import cmdline
from uman_pkg import control


def run_uman():
    """Run uman

    This is the main program. It collects arguments and runs the appropriate
    control module function.
    """
    args = cmdline.parse_args()

    if not args.debug:
        sys.tracebacklimit = 0

    # Run self-tests if requested
    if args.cmd == 'selftest':
        # pylint: disable=import-outside-toplevel
        from u_boot_pylib import test_util
        from uman_pkg import ftest

        to_run = (args.testname if hasattr(args, 'testname') and
                  args.testname not in [None, 'selftest'] else None)
        result = test_util.run_test_suites(
            'uman', args.debug, args.verbose, args.no_capture,
            args.test_preserve_dirs, None, to_run, None,
            [ftest.TestUmanCmdline, ftest.TestBuildSubcommand,
             ftest.TestUmanCIVars, ftest.TestUmanCI,
             ftest.TestUmanControl, ftest.TestGitLabParser,
             ftest.TestUmanMergeRequest, ftest.TestSettings,
             ftest.TestCcSubcommand, ftest.TestSetupSubcommand,
             ftest.TestMain])
        sys.exit(0 if result.wasSuccessful() else 1)

    # Run the appropriate command
    exit_code = control.run_command(args)
    sys.exit(exit_code)


if __name__ == '__main__':
    sys.exit(run_uman())
