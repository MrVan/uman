# SPDX-License-Identifier: GPL-2.0+
# Copyright 2025 Canonical Ltd
# Written by Simon Glass <simon.glass@canonical.com>

"""Docker command for running U-Boot tests in a CI Docker container

This module handles the 'docker' subcommand which runs U-Boot pytest
tests inside the same Docker image used by CI.
"""

import os

import yaml

# pylint: disable=import-error
from u_boot_pylib import command
from u_boot_pylib import tout

from uman_pkg import util


# Template key in .gitlab-ci.yml
CI_TEMPLATE = '.buildman_and_testpy_template'

# CI variable substitutions (variable name -> default value)
CI_SUBS = {
    'CI_PROJECT_DIR': '/source',
    'OVERRIDE': '',
    'BUILD_ENV': '',
    'TEST_PY_ID': '',
    'TEST_PY_EXTRA': '',
}


def load_ci_yaml(uboot_dir):
    """Load and parse .gitlab-ci.yml from a U-Boot tree

    Args:
        uboot_dir (str): Path to U-Boot source directory

    Returns:
        dict: Parsed YAML data, or None if not found
    """
    ci_path = os.path.join(uboot_dir, '.gitlab-ci.yml')
    if not os.path.exists(ci_path):
        tout.error(f'Cannot find {ci_path}')
        return None

    with open(ci_path, 'r', encoding='utf-8') as inf:
        return yaml.safe_load(inf)


def get_ci_image(data):
    """Extract the CI Docker image from parsed .gitlab-ci.yml data

    Looks for the default image and expands ${MIRROR_DOCKER} to
    docker.io.

    Args:
        data (dict): Parsed YAML data

    Returns:
        str: Full Docker image path, or None if not found
    """
    image = data.get('image')
    if not image:
        default = data.get('default', {})
        image = default.get('image') if isinstance(default, dict) else None
    if not image:
        tout.error('Cannot find image in .gitlab-ci.yml')
        return None

    return image.replace('${MIRROR_DOCKER}', 'docker.io')


def get_ci_script(data):
    """Extract before_script and script from the CI test template

    Args:
        data (dict): Parsed YAML data

    Returns:
        tuple: (before_script, script) as lists of shell command strings,
            or (None, None) if not found
    """
    template = data.get(CI_TEMPLATE)
    if not template:
        tout.error(f'Cannot find {CI_TEMPLATE} in .gitlab-ci.yml')
        return None, None

    before = template.get('before_script', [])
    script = template.get('script', [])
    return before, script


def build_script(data, board, test_spec, adjust_cfg=None,
                 pytest_args=None):
    """Generate the shell script to run inside the Docker container

    Parses the before_script and script sections from .gitlab-ci.yml
    and applies variable substitutions for the given board and test spec.

    Args:
        data (dict): Parsed YAML data
        board (str): Board name (e.g. 'sandbox')
        test_spec (str or None): pytest -k filter spec
        adjust_cfg (list or None): Kconfig adjustments for buildman -a
        pytest_args (list or None): Extra pytest flags (e.g. ['-x', '-s'])

    Returns:
        str: Shell script to pass to bash -c, or None on error
    """
    before, script = get_ci_script(data)
    if before is None:
        return None

    # Build substitution map; put -a flags into OVERRIDE so they
    # are appended to the buildman command line
    override = ''
    if adjust_cfg:
        override = ' '.join(f'-a {cfg}' for cfg in adjust_cfg)
    subs = dict(CI_SUBS)
    subs['OVERRIDE'] = override
    subs['TEST_PY_BD'] = board
    subs['TEST_PY_EXTRA'] = ' '.join(pytest_args) if pytest_args else ''

    # Apply substitutions to each command; set test spec as shell
    # variables so bash handles ${VAR:+...} expansions natively
    spec = test_spec or ''
    commands = ['set -e',
                'mkdir -p test/hooks/bin test/hooks/py',
                f'export TEST_PY_TEST_SPEC="{spec}"',
                f'export TEST_SPEC="{spec}"']
    for cmd in before + script:
        for var, val in subs.items():
            cmd = cmd.replace(f'${{{var}}}', val)
        commands.append(cmd)

    return '\n'.join(commands)


def run(args):
    """Run the docker command

    Args:
        args (argparse.Namespace): Parsed arguments

    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    uboot_dir = util.get_uboot_dir()
    if not uboot_dir:
        tout.error('Not in a U-Boot tree and $USRC not set')
        return 1

    # Load CI config
    data = load_ci_yaml(uboot_dir)
    if not data:
        return 1

    # Determine Docker image
    image = args.image
    if not image:
        image = get_ci_image(data)
        if not image:
            return 1

    board = args.board

    uid_gid = command.output_one_line('id', '-u') + ':' + \
        command.output_one_line('id', '-g')

    docker_cmd = ['docker', 'run', '--rm',
                  '--user', uid_gid,
                  '-e', 'HOME=/tmp',
                  '-v', '/etc/passwd:/etc/passwd:ro',
                  '-v', f'{uboot_dir}:/source',
                  '-w', '/source', image]

    if args.interactive:
        docker_cmd.append('bash')
    else:
        spec = ' '.join(args.test_spec) if args.test_spec else None
        extra = []
        if args.exitfirst:
            extra.append('-x')
        if args.show_output:
            extra.append('-s')
        script = build_script(data, board, spec, args.adjust_cfg,
                              extra or None)
        if not script:
            return 1
        docker_cmd.extend(['bash', '-c', script])

    result = util.exec_cmd(docker_cmd, args.dry_run, capture=False)
    if result and result.return_code:
        return result.return_code

    return 0
