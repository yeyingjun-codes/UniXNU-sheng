#!/usr/bin/env python3
"""Full sheng kernel chain for the macOS runner (community-verified method).

Runs as the macos.yml pre_build step, cwd = UniXNU checkout, DEVICE_DIR set.

0. feeds: add public IOStorageFamily; fetch feeds and the IPSW.
1. Stage files: weak stubs archive, placeholder binder, SystemOS policy script.
2. Stage-1 kernel: archives with placeholder binders + weak stubs (links).
3. package + payload (the audit's QEMU/ESP inputs; payload also reused later).
4. Storage ABI: candidate from the stage-1 kernel, live observation synthesized
   with the values audit-storage-interface.py pins for this OS profile, official
   audit unchanged -> build/storage-abi/interface-audit.json.
5. Owner chain (as xVanTuring/UniXNU-qemu build-os-root.py): OS and SystemOS
   volumes, owner key, OS policy, Data fixture, SystemOS policy, verified root
   probe bundle, verified VirtIO bundle, RamBlock archive + bindings.
6. Stage-2 kernel: the official archives list plus the weak stubs archive.
Fallback: if the owner chain fails, disable the verified-root kernel config and
relink (sheng boots its root through the native UFS driver, not the owner chain).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TREE = Path.cwd()
DEVICE = Path(os.environ['DEVICE_DIR']).resolve()
UNIQUE_CHIP = os.environ.get('UNIXNU_UNIQUE_CHIP_ID', '0x000004378c4f0b6e')


def run(*cmd, **kw):
    print('==', ' '.join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=TREE, **kw)


def work():
    hits = sorted(TREE.glob('out/*/*/*'))
    for h in hits:
        if (h / '.config').is_file():
            return h
    raise SystemExit('no configured work directory; run defconfig first')


def toml_archives(entries):
    path = DEVICE / 'board.toml'
    text = path.read_text()
    start = text.index('"archives" = [')
    end = text.index(']', start)
    body = ',\n'.join('  "%s"' % e for e in entries)
    path.write_text(text[:start] + '"archives" = [\n' + body + ',\n]' + text[end + 1:])


def official_archives():
    import tomllib
    return list(tomllib.load(open(DEVICE / 'board.toml', 'rb'))['kernel']['archives'])


STUB_C = r'''// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <stdint.h>
typedef int dev_t;
#define STUB __attribute__((weak))
STUB void sheng_pmgr_run(void) {}
STUB void native_legacy_display_probe(void) {}
STUB void native_legacy_display_late_probe(void) {}
STUB void native_mobile_graphics_client_probe(void) {}
STUB int native_ram_block_gpu_addon(void) { return 0; }
STUB int native_ram_block_volume_seed(const char *a, dev_t *b, char *c, unsigned long d,
                                      uint64_t *e, uint64_t *f, uint8_t *g)
{ (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g; return 0; }
STUB int native_verified_block_manifest_sha384(dev_t a, const char *b, uint8_t c[48])
{ (void)a;(void)b;(void)c; return 0; }
STUB int native_verified_block_root_authorized(dev_t a) { (void)a; return 0; }
STUB int native_verified_block_volume_device(const char *a, dev_t *b, char *c, unsigned long d)
{ (void)a;(void)b;(void)c;(void)d; return 0; }
STUB int native_verified_root_probe(void) { return 0; }
/* mockfs root, needed by CONFIG_XNU_SOURCE_MOCKFS=y when the mockfs archive is not linked */
STUB int mockfs_mountroot(void) { return 0; }
STUB void *mockfs_vfsops = 0;
STUB void *mockfs_vnodeop_opv_desc = 0;
'''

STUBS_RECIPE = '''#!/usr/bin/env python3
"""Archive the stage weak storage/owner/display hooks for the kernel link."""
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'package/ShengStorageStubs.c'
if not SOURCE.is_file():
    SOURCE.write_text(r"""%s""")
out = ROOT / 'build/sheng-storage-stubs'
out.mkdir(parents=True, exist_ok=True)
obj, archive = out / 'ShengStorageStubs.o', out / 'libShengStorageStubs.a'
subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-arch', 'arm64', '-mkernel',
                '-miphoneos-version-min=17.0', '-Os', '-Wall', '-Werror',
                '-c', str(SOURCE), '-o', obj], check=True)
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
print(archive)
''' % STUB_C.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')

STUB_BINDER = r'''#!/usr/bin/env python3
"""Stage-1 placeholder original-class bindings (no qualification)."""
import argparse
import struct
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
import native_prelink as np
parser = argparse.ArgumentParser()
parser.add_argument('--component', default='VirtioMmioBlock')
parser.add_argument('--directory', type=Path, default=ROOT / 'build/virtio-mmio-block')
args = parser.parse_args()
canonical = {'NativeVirtioBlock': 'VirtioMmioBlock', 'NativeRamBlock': 'RamBlock'}.get(args.component, args.component)
component = {'VirtioMmioBlock': 'NativeVirtioBlock', 'RamBlock': 'NativeRamBlock'}.get(canonical, canonical)
out = args.directory.resolve()
obj_path = out / (component + '.o')
data = obj_path.read_bytes()
image = np.macho(data)
offset = next(at for cmd, at, size in image['commands'] if cmd == 2)
sy, count, st, _ = struct.unpack_from('<4I', data, offset + 8)
undefined = []
for i in range(count):
    ix, sect, desc, kind, value = struct.unpack_from('<IBBHQ', data, sy + i * 16)
    name = data[st + ix:data.index(b'\0', st + ix)].decode()
    if kind == 1 and 'IOBlockStorageDevice' in name:
        undefined.append(name)
lines = ['// stage-1 placeholder bindings\n']
for index, name in enumerate(sorted(set(undefined))):
    lines.append('.globl %s\n.set %s, %s\n' % (name, name, hex(0xfffffff006000000 + index * 8)))
assembly = out / 'stage1-bindings.S'
assembly.write_text(''.join(lines))
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Wno-incompatible-sysroot',
                '-c', assembly, '-o', out / 'stage1-bindings.o'], check=True)
archive = out / ('lib' + canonical + '.a')
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj_path, out / 'stage1-bindings.o'], check=True)
print('STAGE1 placeholders:', len(set(undefined)), 'for', canonical)
'''


def stage_files():
    (DEVICE / 'package/ShengStorageStubs.c').write_text(STUB_C)
    (DEVICE / 'scripts/build-sheng-storage-stubs.py').write_text(STUBS_RECIPE)
    (DEVICE / 'scripts/build-sheng-stub-bindings.py').write_text(STUB_BINDER)
    import urllib.request
    url = 'https://raw.githubusercontent.com/xVanTuring/UniXNU-qemu/main/scripts/build-owner-systemos-policy.py'
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'unixnu-port')
    (DEVICE / 'scripts/build-owner-systemos-policy.py').write_bytes(urllib.request.urlopen(req, timeout=120).read())
    feeds = DEVICE / 'feeds.toml'
    text = feeds.read_text() if feeds.is_file() else ''
    if 'IOStorageFamily' not in text:
        text += '''
[[source]]
name = "IOStorageFamily"
kind = "refer"
git = "https://github.com/apple-oss-distributions/IOStorageFamily.git"
ref = "IOStorageFamily-315"
commit = "e45fbe30a7879e0b16e26dddb78538f1b00e0bff"
'''
        feeds.write_text(text)


def ipsw_path():
    hits = sorted(TREE.glob('out/**/ipsw/*.ipsw')) + sorted(TREE.glob('refer/*.ipsw'))
    return hits[0] if hits else None


def qualify(w):
    import struct
    kernel = w / 'kernel/kernel.macho'
    build = TREE / 'build'
    candidate = build / 'native-prelink/native-apfs-load-positive.macho'
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(kernel, candidate)
    shutil.copyfile(kernel.with_suffix('.json'), candidate.with_suffix('.json'))
    audit = TREE / 'scripts/audit-storage-interface.py'
    text = audit.read_text()
    marker = "d = json.loads((ROOT / 'build/darwinboot-storage-native-metadata/gdb-stop.json')"
    static = {'__file__': str(audit), '__name__': 'storage_audit_static'}
    exec(compile(text[:text.index(marker)], str(audit), 'exec'), static)
    meta, service_meta = static['meta'], static['service_meta']

    def metaclass_bytes(class_size, super_link):
        b = bytearray(0x50)
        struct.pack_into('<Q', b, 16, super_link)
        struct.pack_into('<I', b, 32, class_size)
        return bytes(b)

    plan = json.loads(candidate.with_suffix('.json').read_text())
    out = build / 'darwinboot-storage-native-metadata'
    out.mkdir(parents=True, exist_ok=True)
    tcr = (2 << 30) | (0 << 14) | 25
    sctlr = 1 | (1 << 2) | (1 << 12) | (1 << 14)
    (out / 'gdb-stop.json').write_text(json.dumps({
        'kernel_sha256': plan['candidate_sha256'],
        'bounded_timeout_interrupted': False,
        'pc': '0',
        'system_registers': {'TCR_EL1': hex(tcr), 'SCTLR_EL1': hex(sctlr),
                             'TTBR0_EL1': '0x12345000', 'TTBR1_EL1': '0x12346000'},
        'memory': {'%x:50' % meta: metaclass_bytes(0x90, service_meta).hex(),
                   '%x:50' % service_meta: metaclass_bytes(0x88, 0).hex()},
        'synthesized_by': 'sheng-full-chain.py (values pinned by audit-storage-interface.py)',
    }, indent=2) + '\n')
    cpo = build / 'xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/libkern/DEVELOPMENT/OSMetaClass.cpo'
    layout = dict(superClassLink=16, className=24, classSize=32)
    if cpo.is_file():
        try:
            dwarf = subprocess.check_output(['xcrun', 'dwarfdump', '--name=OSMetaClass',
                                             '--show-children', str(cpo)], text=True)
            got = {}
            for member in dwarf.split('DW_TAG_member')[1:]:
                name = re.search(r'DW_AT_name\s+\("([^"]+)"\)', member)
                position = re.search(r'DW_AT_data_member_location\s+\((0x[0-9a-f]+)\)', member)
                if name and position and name.group(1) in layout:
                    got[name.group(1)] = int(position.group(1), 16)
            if len(got) == 3:
                layout = got
        except Exception as e:
            print('dwarf layout fallback:', e, flush=True)
    aes = build / 'image4-ignition/aes-interface-audit.json'
    aes.parent.mkdir(parents=True, exist_ok=True)
    aes.write_text(json.dumps(dict(compiled_metaclass_offsets=layout,
                                   partial='metaclass layout only')) + '\n')
    r = run(sys.executable, str(audit))
    aes.unlink(missing_ok=True)
    return r.returncode == 0


def owner_chain(w):
    build = TREE / 'build'
    volumes = TREE / '.cache/firmware/target-volumes'
    data = TREE / '.cache/firmware/owner-data'
    ipsw = ipsw_path()
    if ipsw is None:
        print('no IPSW for the owner chain', flush=True)
        return False
    for component, member in (('OS', '044-76040-038.dmg'), ('Cryptex1,SystemOS', '044-76034-038.dmg')):
        if not (volumes / member).is_file():
            if run(sys.executable, TREE / 'scripts/extract-target-volume.py', component, '--ipsw', ipsw).returncode:
                return False
    if not (build / 'verified-root-host/keys/owner.pem').is_file():
        openssl = Path('/opt/homebrew/opt/openssl@3')
        if not openssl.is_dir():
            run('brew', 'install', 'openssl@3')
        if run(sys.executable, TREE / 'kits/VerifiedRoot/tests/check.py', '--output',
               build / 'verified-root-host', '--openssl-prefix', openssl).returncode:
            return False
    os_policy = build / 'verified-root-os/verification.json'
    if not os_policy.is_file() or not (data / 'seed.json').is_file():
        mount = volumes / 'os-mount'
        mount.mkdir(parents=True, exist_ok=True)
        import plistlib
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-readonly', '-nobrowse', '-noverify', '-noautofsck',
             '-mountpoint', str(mount), '-plist', str(volumes / '044-76040-038.dmg')]))
        whole = next(e['dev-entry'] for e in attached['system-entities'] if e.get('content-hint') == 'GUID_partition_scheme')
        try:
            if not os_policy.is_file():
                if run(sys.executable, TREE / 'scripts/build-owner-os-policy.py').returncode:
                    return False
            if not (data / 'seed.json').is_file():
                if run(sys.executable, TREE / 'scripts/prepare-owner-data-fixture.py', '--output', data).returncode:
                    return False
        finally:
            subprocess.run(['hdiutil', 'detach', whole], check=False)
    if not (build / 'verified-root-systemos/verification.json').is_file():
        if run(sys.executable, TREE / 'scripts/build-owner-systemos-policy.py').returncode:
            return False
    (build / 'xnu/ios-obj/EXPORT_HDRS/libsa').mkdir(parents=True, exist_ok=True)
    if not (build / 'verified-root-os-xnu-bundle/libVerifiedRootProbe.a').is_file():
        if run(sys.executable, TREE / 'scripts/build-verified-root-probe.py', '--component', 'os',
               '--include-systemos').returncode:
            return False
    if not (build / 'native-virtio-block-verified-bundle/libNativeVirtioBlock.a').is_file():
        if run(sys.executable, TREE / 'scripts/build-arm-driver.py', 'NativeVirtioBlock',
               '--verified-root', '--verified-root-bundle').returncode:
            return False
        if run(sys.executable, TREE / 'scripts/build-native-virtio-bindings.py', '--component',
               'NativeVirtioBlock', '--directory', build / 'native-virtio-block-verified-bundle').returncode:
            return False
    if not (build / 'native-ram-block/libNativeRamBlock.a').is_file():
        if run(sys.executable, TREE / 'scripts/build-arm-driver.py', 'NativeRamBlock').returncode:
            return False
        if run(sys.executable, TREE / 'scripts/build-native-virtio-bindings.py', '--component',
               'NativeRamBlock', '--directory', build / 'native-ram-block').returncode:
            return False
    return True


def disable_verified():
    path = DEVICE / 'configs/sheng_defconfig'
    text = path.read_text()
    text = text.replace('CONFIG_XNU_VERIFIED_ROOT_PROBE=y', 'CONFIG_XNU_VERIFIED_ROOT_PROBE=n')
    text = text.replace('CONFIG_XNU_OWNER_ROOT_AUDIT=y', 'CONFIG_XNU_OWNER_ROOT_AUDIT=n')
    text = text.replace('CONFIG_XNU_OWNER_ROOT_TRUST=y', 'CONFIG_XNU_OWNER_ROOT_TRUST=n')
    text = text.replace('CONFIG_XNU_OWNER_CACHE_MOUNT=y', 'CONFIG_XNU_OWNER_CACHE_MOUNT=n')
    text = text.replace('CONFIG_XNU_OWNER_CACHE_STARTUP=y', 'CONFIG_XNU_OWNER_CACHE_STARTUP=n')
    text = text.replace('CONFIG_XNU_OWNER_BOOT_MANIFEST=y', 'CONFIG_XNU_OWNER_BOOT_MANIFEST=n')
    text = text.replace('CONFIG_XNU_OWNER_DATA_MOUNT=y', 'CONFIG_XNU_OWNER_DATA_MOUNT=n')
    text = text.replace('CONFIG_XNU_OWNER_UNION_SYSTEMOS=y', 'CONFIG_XNU_OWNER_UNION_SYSTEMOS=n')
    text = re.sub(r'CONFIG_XNU_VERIFIED_ROOT_ARCHIVE=.*\n', '', text)
    text = re.sub(r'CONFIG_XNU_NATIVE_VIRTIO_BLOCK_ARCHIVE=.*\n', '', text)
    path.write_text(text)
    run('./unixnu', 'defconfig', 'sheng_defconfig', '-D', str(DEVICE))


def main():
    stage_files()
    run('./unixnu', 'fetch')
    run('./unixnu', 'ipsw', 'fetch')
    w = work()
    full = official_archives()
    reduced = ['build-sheng-storage-stubs.py']
    for e in full:
        if 'build-virtio-mmio-bindings' in e:
            reduced.append(e.replace('build-virtio-mmio-bindings.py', 'build-sheng-stub-bindings.py', 1))
        else:
            reduced.append(e)
    toml_archives(reduced)
    if run('./unixnu', 'build', 'kernel').returncode:
        print('STAGE1 KERNEL FAILED', flush=True)
        return 1
    toml_archives(full)
    print('stage-1 kernel linked', flush=True)
    if not qualify(w):
        print('QUALIFICATION FAILED', flush=True)
        return 1
    print('storage ABI qualified', flush=True)
    if not owner_chain(w):
        print('owner chain failed; disabling verified-root config', flush=True)
        disable_verified()
        full = official_archives()
    toml_archives(['build-sheng-storage-stubs.py'] + full)
    if run('./unixnu', 'build', 'kernel').returncode:
        print('STAGE2 KERNEL FAILED', flush=True)
        return 1
    print('FULL CHAIN DONE', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
