#!/usr/bin/env python3
"""Build the Data seed that CONFIG_XNU_OWNER_DATA_MOUNT requires, from the IPSW OS volume.

The seed is the repository's own format: a small APFS "OwnerData" container holding the OS
volume's initial /private/var, packed by drivers/DarwinBoot/Test/packed_ramdisk and grown to
the requested size by scripts/seed-data-grow.py. No board file and no official script changes.

usage: make-data-seed.py OS_DMG OUTPUT.dbr UNI_XNU_ROOT [--gib N]
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

IGNORED = {'staged_system_apps'}
EXTRA_DIRS = ('keybags', 'mobile/tmp', 'tmp', 'run')
SKELETON = (
    'db', 'db/dslocal', 'db/dslocal/nodes', 'db/dslocal/nodes/Default',
    'db/dslocal/nodes/Default/users', 'db/dslocal/nodes/Default/groups',
    'db/dslocal/nodes/Default/config',
    'containers', 'containers/Data', 'containers/Data/System',
    'containers/Data/Application', 'containers/Shared', 'containers/Shared/SystemGroup',
    'PersonaVolumes', 'preboot', 'root', 'Library',
    'keybags/backup', 'mobile/Library/Preferences', 'mobile/Library/Caches',
    'mobile/Library/Logs', 'mobile/Containers', 'mobile/Documents', 'mobile/Media',
)
BASE_MIB = 32


def run(args):
    """Run a command, print it, and fail loudly with its output."""
    print('+ ' + ' '.join(str(a) for a in args), flush=True)
    done = subprocess.run([str(a) for a in args], capture_output=True)
    out = done.stdout.decode('utf-8', 'replace')
    err = done.stderr.decode('utf-8', 'replace')
    if out.strip():
        print(out.strip()[:4000], flush=True)
    if err.strip():
        print(err.strip()[-4000:], flush=True)
    if done.returncode != 0:
        raise SystemExit(f'command failed ({done.returncode}): {args[0]}')
    return out


def attach(image, mountpoint=None, readonly=False):
    """Attach an image and return (whole-disk device, mounted volume paths)."""
    args = ['hdiutil', 'attach']
    if readonly:
        args.append('-readonly')
    args += ['-noverify', '-noautofsck']
    if mountpoint is not None:
        args += ['-mountpoint', str(mountpoint)]
    args.append(image)
    out = run(args)
    device = None
    volumes = []
    for line in out.splitlines():
        match = re.match(r'(/dev/disk\d+)\s', line)
        if match and device is None:
            device = match.group(1)
        fields = [part for part in re.split(r'\t+', line) if part]
        if len(fields) >= 3 and fields[0].startswith('/dev/disk') and fields[2].startswith('/Volumes'):
            volumes.append(fields[2])
    if device is None:
        raise SystemExit('could not find the attached disk device in hdiutil output')
    return device, volumes


def detach(device):
    subprocess.run(['hdiutil', 'detach', device, '-force'], capture_output=True)
    print(f'detached {device}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('os_dmg', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('unixnu', type=Path)
    parser.add_argument('--gib', type=int, default=8)
    args = parser.parse_args()

    work = args.output.resolve().parent / 'seed-work'
    work.mkdir(parents=True, exist_ok=True)
    base = work / 'owner-data-base.cdr'
    base_dbr = work / 'owner-data-base.dbr'
    if args.output.exists():
        raise SystemExit('Refuse to overwrite ' + str(args.output))

    print('creating the 32 MiB APFS OwnerData skeleton', flush=True)
    run(['hdiutil', 'create', '-size', f'{BASE_MIB}m', '-layout', 'NONE', '-fs', 'APFS',
         '-volname', 'OwnerData', '-type', 'UDTO', '-ov', base])

    count = total = 0
    os_dev = data_dev = None
    try:
        os_dev, volumes = attach(args.os_dmg, readonly=True)
        print('mounted:' + (', '.join(volumes) if volumes else ' none'), flush=True)
        source = None
        for path in volumes:
            candidate = Path(path) / 'private/var'
            if candidate.is_dir():
                source = candidate
                break
        if source is None and volumes:
            source = Path(volumes[0]) / 'private/var'

        data_dev, data_volumes = attach(base)
        if len(data_volumes) != 1:
            raise SystemExit(f'the skeleton mounted {len(data_volumes)} volumes: {data_volumes}')
        target = Path(data_volumes[0])

        if source is not None and source.is_dir():
            print(f'copying the initial var tree of {source}', flush=True)
            for current, dirs, files in os.walk(source, followlinks=False):
                dirs[:] = [name for name in dirs if name not in IGNORED]
                dest = target / Path(current).relative_to(source)
                dest.mkdir(parents=True, exist_ok=True)
                os.chmod(dest, stat.S_IMODE(Path(current).stat().st_mode))
                for name in list(dirs) + files:
                    src, dst = Path(current) / name, dest / name
                    if src.is_symlink():
                        os.symlink(os.readlink(src), dst)
                        if name in dirs:
                            dirs.remove(name)
                    elif src.is_file():
                        subprocess.run(['cp', '-p', str(src), str(dst)], check=True)
                        count += 1
                        total += src.stat().st_size
        else:
            print('warning: no initial /private/var in the OS volume; writing the empty skeleton', flush=True)
        for name in SKELETON + EXTRA_DIRS:
            (target / name).mkdir(parents=True, exist_ok=True)
        os.chmod(target / 'tmp', 0o1777)
        print(f'seed content: {count} files, {total} bytes', flush=True)
    finally:
        if data_dev:
            detach(data_dev)
        if os_dev:
            detach(os_dev)

    data = base.read_bytes()
    if len(data) != BASE_MIB * 1024**2 or data[32:36] != b'NXSB':
        raise SystemExit('host formatting did not produce a raw APFS container')

    sys.path.insert(0, str(args.unixnu / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk

    metadata = packed_ramdisk.pack(base, base_dbr)
    print('packed base seed: ' + json.dumps(metadata, default=str), flush=True)

    print(f'growing the container to {args.gib} GiB', flush=True)
    run([sys.executable, args.unixnu / 'scripts/seed-data-grow.py',
         '--gib', str(args.gib), base_dbr, args.output])

    report = dict(output=str(args.output), bytes=args.output.stat().st_size, gib=args.gib,
                  source_files=count, source_bytes=total)
    (args.output.parent / 'data-seed.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
