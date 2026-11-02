#!/usr/bin/env python3
"""Build the Data seed that CONFIG_XNU_OWNER_DATA_MOUNT requires, from the IPSW OS volume.

The seed is the repository's own format: an APFS "OwnerData" container holding the OS volume's
initial /private/var, packed by drivers/DarwinBoot/Test/packed_ramdisk. The container is created
at the final size, so no resize step and no container discovery are involved.

usage: make-data-seed.py OS_DMG OUTPUT.dbr UNI_XNU_ROOT [--gib N]
"""
import argparse
import json
import os
import re
import shutil
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


def run(args):
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


def attach(image, readonly=False):
    args = ['hdiutil', 'attach']
    if readonly:
        args.append('-readonly')
    args += ['-noverify', '-noautofsck', image]
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
    container = work / f'owner-data-{args.gib}g.cdr'
    for path in (args.output, container):
        if path.exists():
            path.unlink()

    print(f'creating the {args.gib} GiB APFS OwnerData container', flush=True)
    run(['hdiutil', 'create', '-size', f'{args.gib}g', '-layout', 'NONE', '-fs', 'APFS',
         '-volname', 'OwnerData', '-type', 'UDTO', '-ov', container])

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

        data_dev, data_volumes = attach(container)
        mounted = [p for p in data_volumes if Path(p).name == 'OwnerData'] or data_volumes
        if not mounted:
            raise SystemExit('the container mounted no volume')
        target = Path(mounted[0])
        print(f'owner data volume {target}', flush=True)

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
                        shutil.copy2(src, dst)
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

    with container.open('rb') as handle:
        header = handle.read(4096)
    if len(header) < 4096 or header[32:36] != b'NXSB':
        raise SystemExit('host formatting did not produce an APFS container')
    block_size = int.from_bytes(header[36:40], 'little')
    blocks = int.from_bytes(header[40:48], 'little')
    print(f'container {blocks * block_size} bytes, block size {block_size}', flush=True)

    sys.path.insert(0, str(args.unixnu / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk

    metadata = packed_ramdisk.pack(container, args.output)
    print('packed seed: ' + json.dumps(metadata, default=str), flush=True)

    extra = {k: v for k, v in metadata.items() if k not in ('packed', 'bytes')}
    report = dict(output=str(args.output), bytes=args.output.stat().st_size, gib=args.gib,
                  container_bytes=blocks * block_size, source_files=count,
                  source_bytes=total, **extra)
    (args.output.parent / 'data-seed.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
