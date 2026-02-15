# SPDX-License-Identifier: GPL-2.0+
# Copyright 2026 Canonical Ltd
# Written by Simon Glass <simon.glass@canonical.com>

"""Claude Code container management

This module handles the 'claude-code' subcommand which creates and manages LXC
containers for running Claude Code.
"""

import os
import random
import string

# pylint: disable=import-error
from u_boot_pylib import tout

from uman_pkg import settings
from uman_pkg.util import exec_cmd


# Container home directory
UBUNTU_HOME = '/home/ubuntu'

# Project mount point inside the container
PROJECT_DEST = f'{UBUNTU_HOME}/project'


def get_uman_dir():
    """Get the uman installation directory

    Returns:
        str: Absolute path to the uman package parent directory
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_essential_mounts(project_src):
    """Get the list of hardcoded essential mounts

    Args:
        project_src (str): Absolute path to the project source directory

    Returns:
        list of tuple: (name, source, dest) triples
    """
    home = os.path.expanduser('~')
    uman_dir = get_uman_dir()
    mounts = [
        ('datadir', project_src, PROJECT_DEST),
        ('claudejson', os.path.join(home, '.claude.json'),
         f'{UBUNTU_HOME}/.claude.json'),
        ('claudedir', os.path.join(home, '.claude'),
         f'{UBUNTU_HOME}/.claude'),
        ('hostbin', os.path.join(home, 'bin'), f'{UBUNTU_HOME}/bin'),
        ('uman', uman_dir, uman_dir),
        ('uboottools', os.path.realpath(os.path.expanduser(
            os.environ.get('UBOOT_TOOLS', '~/u/tools'))),
         f'{UBUNTU_HOME}/u/tools'),
    ]
    for fname, mname in [('.gitconfig', 'gitconfig'),
                          ('.buildman', 'buildman'),
                          ('.buildman-toolchains', 'toolchains')]:
        path = os.path.join(home, fname)
        if os.path.exists(path):
            mounts.append((mname, path, f'{UBUNTU_HOME}/{fname}'))
    return mounts


def get_config_mounts():
    """Parse [claude-code] mounts from ~/.uman config

    Mount format: name:source:dest (one per line in the mounts value)

    Returns:
        list of tuple: (name, source, dest) triples
    """
    cfg = settings.get_all()
    if not cfg.has_section('claude-code'):
        return []

    raw = cfg.get('claude-code', 'mounts', fallback='')
    mounts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(':')
        if len(parts) != 3:
            tout.warning(f'Ignoring malformed mount: {line}')
            continue
        name, source, dest = parts
        source = os.path.expandvars(os.path.expanduser(source))
        mounts.append((name, source, dest))
    return mounts


def get_git_symlink_mount(project_src):
    """Handle .git symlink by creating a mount for the real target

    If .git is a symlink, the container needs access to the real target
    so that git operations work correctly.

    Args:
        project_src (str): Absolute path to the project source directory

    Returns:
        tuple or None: (name, source, dest) if .git is a symlink, else None
    """
    git_path = os.path.join(project_src, '.git')
    if not os.path.islink(git_path):
        return None

    git_link = os.readlink(git_path)
    git_real = os.path.realpath(git_path)

    # Resolve the same relative symlink from the container mount point
    git_container = os.path.normpath(
        os.path.join(PROJECT_DEST, git_link))
    return ('dotgit', git_real, git_container)


def gen_name(base):
    """Generate a random container name

    Args:
        base (str): Ubuntu base image name (e.g. 'noble')

    Returns:
        str: Container name like 'ubuntu-noble-a1b2'
    """
    suffix = ''.join(random.choices(string.hexdigits[:16], k=4))
    return f'ubuntu-{base}-{suffix}'


def container_exists(name):
    """Check whether an LXC container exists

    Args:
        name (str): Container name

    Returns:
        bool: True if the container exists
    """
    result = exec_cmd(['lxc', 'info', name], dry_run=False)
    return result is not None and result.return_code == 0


def container_status(name):
    """Get the status of an LXC container

    Args:
        name (str): Container name

    Returns:
        str or None: Status string (e.g. 'RUNNING', 'STOPPED') or None
    """
    result = exec_cmd(['lxc', 'info', name], dry_run=False)
    if not result or result.return_code:
        return None
    for line in result.stdout.splitlines():
        if line.startswith('Status: '):
            return line.split(': ', 1)[1].strip().upper()
    return None


def lxc(*args, dry_run=False):
    """Run an lxc command

    Args:
        *args: Arguments to pass to lxc
        dry_run (bool): If True, just show command

    Returns:
        CommandResult or None
    """
    return exec_cmd(['lxc'] + list(args), dry_run)


def lxc_exec(name, cmd, dry_run=False, user=None):
    """Run a command inside the container

    Args:
        name (str): Container name
        cmd (str): Shell command to execute
        dry_run (bool): If True, just show command
        user (str or None): User to run as (default: root)

    Returns:
        CommandResult or None
    """
    lxc_cmd = ['lxc', 'exec', name, '--']
    if user:
        lxc_cmd += ['sudo', '-u', user, 'bash', '-c', cmd]
    else:
        lxc_cmd += ['bash', '-c', cmd]
    return exec_cmd(lxc_cmd, dry_run)


def create_container(name, base, dry_run=False):
    """Create a new LXC container with uid/gid mapping

    Args:
        name (str): Container name
        base (str): Ubuntu base image name
        dry_run (bool): If True, just show commands
    """
    lxc('init', '-q', f'ubuntu:{base}', name, dry_run=dry_run)

    uid = str(os.getuid())
    gid = str(os.getgid())
    idmap = f'uid {uid} 1000\ngid {gid} 1000'
    if dry_run:
        tout.notice(
            f'printf {idmap!r} | lxc config set -q {name} raw.idmap -')
        return
    import subprocess  # pylint: disable=import-outside-toplevel
    proc = subprocess.run(
        ['lxc', 'config', 'set', '-q', name, 'raw.idmap', '-'],
        input=idmap.encode(), check=False, capture_output=True)
    if proc.returncode:
        tout.error(f'Failed to set idmap: '
                   f'{proc.stderr.decode("utf-8", errors="replace")}')


def has_mount(name, mount_name):
    """Check whether a container already has a named device

    Args:
        name (str): Container name
        mount_name (str): Device name

    Returns:
        bool: True if the device exists
    """
    result = exec_cmd(
        ['lxc', 'config', 'device', 'get', name, mount_name, 'source'],
        dry_run=False)
    return result is not None and result.return_code == 0


def add_mount(name, mount_name, source, path, dry_run=False):
    """Add a disk device to the container if not already present

    Args:
        name (str): Container name
        mount_name (str): Device name
        source (str): Host path
        path (str): Container path
        dry_run (bool): If True, just show command
    """
    if not dry_run and has_mount(name, mount_name):
        return
    lxc('config', 'device', 'add', '-q', name, mount_name, 'disk',
        f'source={source}', f'path={path}', dry_run=dry_run)


def wait_for_user(name, dry_run=False):
    """Wait until the ubuntu user exists in the container

    Args:
        name (str): Container name
        dry_run (bool): If True, just show command
    """
    if dry_run:
        tout.notice('# wait for ubuntu user')
        return
    import time  # pylint: disable=import-outside-toplevel
    while True:
        result = exec_cmd(
            ['lxc', 'exec', name, '--', 'id', '-u', 'ubuntu'],
            dry_run=False)
        if result and result.return_code == 0:
            break
        time.sleep(0.5)


def setup_container(name, dry_run=False):
    """Set up the container after first boot

    Fix permissions, install terminfo, create host user symlink.

    Args:
        name (str): Container name
        dry_run (bool): If True, just show commands
    """
    lxc_exec(name, 'chown ubuntu:ubuntu /home/ubuntu', dry_run=dry_run)

    # Install terminfo from host
    if not dry_run:
        import subprocess  # pylint: disable=import-outside-toplevel
        infocmp = subprocess.run(['infocmp', '-x'], capture_output=True,
                                 check=False)
        if infocmp.returncode == 0:
            tic = subprocess.run(
                ['lxc', 'exec', name, '--', 'tic', '-x', '-'],
                input=infocmp.stdout, capture_output=True, check=False)
            if tic.returncode:
                tout.warning('Could not install terminfo')
    else:
        tout.notice(f'infocmp -x | lxc exec {name} -- tic -x -')

    # Symlink host user home to ubuntu home
    user = os.environ.get('USER', 'ubuntu')
    lxc_exec(name,
             f'test -e /home/{user} || ln -s /home/ubuntu /home/{user}',
             dry_run=dry_run)


def install_tools(name, packages=None, dry_run=False):
    """Install build tools if not already present

    Args:
        name (str): Container name
        packages (str or None): Space-separated package names
        dry_run (bool): If True, just show command
    """
    if not packages:
        packages = 'build-essential'
    cmd = (f'command -v gcc >/dev/null 2>&1 || '
           f'(apt-get update -qq && apt-get install -yqq {packages})')
    lxc_exec(name, cmd, dry_run=dry_run)


def install_claude(name, dry_run=False):
    """Install Claude Code if not already present

    Args:
        name (str): Container name
        dry_run (bool): If True, just show command
    """
    cmd = ('export PATH="$HOME/.local/bin:$HOME/bin:$PATH" && '
           'command -v claude >/dev/null 2>&1 || '
           '(echo "Installing claude..." && '
           'curl -fsSL https://claude.ai/install.sh | bash)')
    lxc_exec(name, cmd, dry_run=dry_run, user='ubuntu')


def setup_uman(name, uboot_tools=None, dry_run=False):
    """Set up uman aliases and bashrc inside the container

    Args:
        name (str): Container name
        uboot_tools (str or None): Path to U-Boot tools inside container
        dry_run (bool): If True, just show commands
    """
    if not uboot_tools:
        uboot_tools = f'{UBUNTU_HOME}/u/tools'

    # Run setup aliases into ~/.local/bin (container-local, not the
    # host-mounted ~/bin whose symlinks use host-specific paths)
    uman_dir = get_uman_dir()
    um_path = os.path.join(uman_dir, 'um')
    setup_cmd = (
        f'export PATH="$HOME/.local/bin:$HOME/bin:$PATH" && '
        f'export UBOOT_TOOLS="{uboot_tools}" && '
        f'{um_path} -q setup aliases -d ~/.local/bin -f')
    lxc_exec(name, setup_cmd, dry_run=dry_run, user='ubuntu')

    # Add uman config to bashrc
    bashrc_block = (
        f'\n# uman setup\n'
        f'export PATH="$HOME/bin:$HOME/.local/bin:$PATH"\n'
        f'export UBOOT_TOOLS="{uboot_tools}"\n'
        f'um() {{ b="$b" USRC="$USRC" command um "$@"; }}\n'
        f'eval "$(um git -a)"\n')

    add_cmd = (
        f"grep -q 'um git -a' ~/.bashrc 2>/dev/null || "
        f"cat >> ~/.bashrc <<'BASHEOF'{bashrc_block}BASHEOF")
    lxc_exec(name, add_cmd, dry_run=dry_run, user='ubuntu')


def launch_shell(name, shell_command=None, dry_run=False):
    """Open an interactive shell or run a command in the container

    Args:
        name (str): Container name
        shell_command (str or None): Command to run, or None for
            interactive shell
        dry_run (bool): If True, just show command
    """
    shell_cmd = shell_command or 'exec bash'
    cmd = ['lxc', 'exec', name, '--', 'sudo', '-iu', 'ubuntu',
           'bash', '-ic', f'cd {PROJECT_DEST} && {shell_cmd}']
    exec_cmd(cmd, dry_run, capture=False)


def launch_claude(name, cont=False, dry_run=False):
    """Launch Claude Code in the container

    Args:
        name (str): Container name
        cont (bool): If True, continue the most recent conversation
        dry_run (bool): If True, just show command
    """
    flag = ' --continue' if cont else ''
    cmd = ['lxc', 'exec', name, '--', 'sudo', '-iu', 'ubuntu', 'bash', '-ic',
           f'cd {PROJECT_DEST} && claude --dangerously-skip-permissions'
           f'{flag}']
    exec_cmd(cmd, dry_run, capture=False)


def delete_container(name, dry_run=False):
    """Force-delete a container

    Args:
        name (str): Container name
        dry_run (bool): If True, just show command
    """
    lxc('delete', '-f', name, dry_run=dry_run)


def get_project(name):
    """Get the project source path for a container

    Args:
        name (str): Container name

    Returns:
        str: Project source path, or '' if not found
    """
    result = exec_cmd(
        ['lxc', 'config', 'device', 'get', name, 'datadir', 'source'],
        dry_run=False)
    if result and result.return_code == 0:
        return result.stdout.strip()
    return ''


def list_containers():
    """List uman containers (those with a datadir device)

    Returns:
        list of tuple: (name, status, project) triples
    """
    result = exec_cmd(['lxc', 'list', '--format', 'csv', '-c', 'ns'],
                       dry_run=False)
    if not result or result.return_code:
        return []
    containers = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) >= 2:
            project = get_project(parts[0])
            if project:
                containers.append((parts[0], parts[1], project))
    return containers


def add_all_mounts(name, project_src, dry_run=False):
    """Add all mounts (essential, git symlink, and config) to a container

    Skips any devices that already exist.

    Args:
        name (str): Container name
        project_src (str): Absolute path to the project source directory
        dry_run (bool): If True, just show commands
    """
    for mname, source, dest in get_essential_mounts(project_src):
        add_mount(name, mname, source, dest, dry_run)

    # Per-container projects dir so --continue is scoped correctly
    home = os.path.expanduser('~')
    proj_dir = os.path.join(home, '.claude', 'cc', name)
    os.makedirs(proj_dir, exist_ok=True)
    add_mount(name, 'claudeproj', proj_dir,
              f'{UBUNTU_HOME}/.claude/projects', dry_run)

    git_mount = get_git_symlink_mount(project_src)
    if git_mount:
        add_mount(name, *git_mount, dry_run)

    for mname, source, dest in get_config_mounts():
        add_mount(name, mname, source, dest, dry_run)


def ensure_running(name, existed, dry_run=False):
    """Start the container if it is not already running

    Args:
        name (str): Container name
        existed (bool): Whether the container existed before this run
        dry_run (bool): If True, just show commands
    """
    if dry_run or not existed:
        lxc('start', name, dry_run=dry_run)
        return

    status = container_status(name)
    if status != 'RUNNING':
        tout.notice(f'Starting container (was {status})')
        lxc('start', name)


def show_containers():
    """List uman containers with their project paths

    Returns:
        int: Exit code
    """
    containers = list_containers()
    if not containers:
        tout.notice('No uman containers found')
    else:
        home = os.path.expanduser('~')
        for cname, status, project in containers:
            if project.startswith(home):
                project = '~' + project[len(home):]
            tout.notice(f'{cname}  {status:8s}  {project}')
    return 0


def run(args):  # pylint: disable=too-many-locals,too-many-branches
    """Main entry point for the cc subcommand

    Creates a container, sets it up, and launches Claude Code or a shell.
    Ephemeral containers are deleted on exit (including Ctrl+C).

    Args:
        args (argparse.Namespace): Parsed arguments

    Returns:
        int: Exit code
    """
    if args.list_containers:
        return show_containers()

    if args.delete:
        if not args.name:
            tout.error('Container name required for --delete')
            return 1
        delete_container(args.name, args.dry_run)
        return 0

    dry_run = args.dry_run

    # Get config values
    cfg = settings.get_all()
    packages = None
    uboot_tools = None
    if cfg.has_section('claude-code'):
        packages = cfg.get('claude-code', 'packages', fallback=None)
        uboot_tools = cfg.get('claude-code', 'uboot_tools', fallback=None)

    # Use config base if user didn't override on command line
    base = args.base
    if base == 'noble' and cfg.has_section('claude-code'):
        base = cfg.get('claude-code', 'base', fallback=base)

    project_src = os.path.realpath(os.getcwd())

    # Ephemeral gets a random name; otherwise use explicit or dir name
    if args.ephemeral:
        name = gen_name(base)
        keep = False
    else:
        name = args.name or os.path.basename(project_src)
        keep = True

    # Check if container already exists
    existed = not dry_run and container_exists(name)
    if existed:
        tout.notice(f'Reusing container: {name}')
    else:
        tout.notice(f'Container: {name}')

    try:
        if not existed:
            create_container(name, base, dry_run)

        add_all_mounts(name, project_src, dry_run)
        ensure_running(name, existed, dry_run)

        # Wait for user and set up (idempotent operations)
        wait_for_user(name, dry_run)
        setup_container(name, dry_run)
        install_tools(name, packages, dry_run)
        install_claude(name, dry_run)
        setup_uman(name, uboot_tools, dry_run)

        # Launch
        if args.shell:
            shell_cmd = args.shell if args.shell is not True else None
            launch_shell(name, shell_cmd, dry_run)
        else:
            launch_claude(name, args.cont, dry_run)

    finally:
        # Only delete ephemeral containers that we created
        if not keep and not existed:
            delete_container(name, dry_run)

    return 0
