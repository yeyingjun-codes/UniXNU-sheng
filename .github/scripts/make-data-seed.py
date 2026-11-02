#!/usr/bin/env python3
"""Build the Data seed that CONFIG_XNU_OWNER_DATA_MOUNT requires, from the IPSW OS volume.

The seed is the official DBRDISK1 packed format: a small APFS "OwnerData" container that
holds the OS volume's initial /private/var, packed by drivers/DarwinBoot/Test/packed_ramdisk.

usage: make-data-seed.py OS_DMG OUTPUT.dbr UNI_XNU_ROOT [--gib N]
"""
import argparse
import json
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

IGNORED = {'staged_system_apps'}
EXTRA_DIRS = ('keybags', 'mobile/tmp', 'tmp', 'run')
BASE_MIB = 32


def sh(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def attach(image, mountpoint=None, readonly=False):
    args = ['hdiutil', 'attach', '-nobrowse', '-owners', 'off', '-plist']
    if readonly:
        args.insert(1, '-readonly')
    if mountpoint is not None:
        args += ['-mountpoint', str(mountpoint)]
    info = plistlib.loads(subprocess.check_output(args + [str(image)]))
    entity = info['system-entities'][0]
    return info, entity['dev-entry']


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

    os_mount = work / 'os-mount'
    data_mount = work / 'data-mount'
    for path in (os_mount, data_mount):
        path.mkdir(exist_ok=True)

    print('creating the 32 MiB APFS OwnerData skeleton', flush=True)
    sh('hdiutil', 'create', '-size', f'{BASE_MIB}m', '-layout', 'NONE', '-fs', 'APFS',
       '-volname', 'OwnerData', '-type', 'UDTO', '-ov', str(base))

    os_info = data_info = None
    try:
        os_info, os_dev = attach(args.os_dmg, readonly=True)
        volumes = [e for e in os_info['system-entities'] if e.get('mount-point')]
        if len(volumes) != 1:
            raise SystemExit(f'expected one mounted OS volume, saw {len(volumes)}')
        source = Path(volumes[0]['mount-point']) / 'private/var'
        if not (source / 'db').is_dir():
            raise SystemExit(f'{source} does not look like the OS initial var tree')

        data_info, data_dev = attach(base)
        target = data_mount
        count = total = 0
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
        for name in EXTRA_DIRS:
            (target / name).mkdir(parents=True, exist_ok=True)
        os.chmod(target / 'tmp', 0o1777)
        print(f'copied {count} files, {total} bytes of initial /private/var', flush=True)
    finally:
        if data_info is not None:
            subprocess.run(['hdiutil', 'detach', data_info['system-entities'][0]['dev-entry'], '-force'],
                           capture_output=True)
        if os_info is not None:
            subprocess.run(['hdiutil', 'detach', os_info['system-entities'][0]['dev-entry'], '-force'],
                           capture_output=True)

    data = base.read_bytes()
    if len(data) != BASE_MIB * 1024**2 or data[32:36] != b'NXSB':
        raise SystemExit('host formatting did not produce a raw APFS container')

    sys.path.insert(0, str(args.unixnu / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk

    metadata = packed_ramdisk.pack(base, base_dbr)
    print('packed base seed:', json.dumps(metadata), flush=True)

    print(f'growing the container to {args.gib} GiB', flush=True)
    subprocess.run([sys.executable, str(args.unixnu / 'scripts/seed-data-grow.py'),
                    '--gib', str(args.gib), str(base_dbr), str(args.output)], check=True)

    report = dict(output=str(args.output), bytes=args.output.stat().st_size,
                  gib=args.gib, source_files=count, source_bytes=total, **metadata)
    (args.output.parent / 'data-seed.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
