#!/usr/bin/env python3
"""Make UniXNURamDiskDxe publish the FAT payload wherever it sits inside the bootloader initrd.

On sheng the bootloader concatenates the vendor ramdisk in front of the boot image ramdisk, so the
payload FAT does not start at linux,initrd-start. The driver otherwise publishes the range as-is and
the FAT driver then finds no boot sector. This patch keeps the published range honest: it clamps the
end down to a whole sector and, when the first sector is not a FAT boot sector, scans the initrd on
sector boundaries for one.
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path('UniXNU')
target = ROOT / 'package/UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.c'
source = target.read_text()

if 'FindFatStart' in source:
    print('already patched')
    raise SystemExit(0)

HELPERS = '''
STATIC
BOOLEAN
IsFatBootSector (
  IN CONST UINT8  *Sector
  )
{
  if ((Sector[510] != 0x55) || (Sector[511] != 0xAA)) {
    return FALSE;
  }

  if ((Sector[0] != 0xEB) && (Sector[0] != 0xE9)) {
    return FALSE;
  }

  if ((CompareMem (Sector + 54, "FAT12   ", 8) == 0) ||
      (CompareMem (Sector + 54, "FAT16   ", 8) == 0) ||
      (CompareMem (Sector + 82, "FAT32   ", 8) == 0))
  {
    return TRUE;
  }

  return FALSE;
}

STATIC
UINT64
FindFatStart (
  IN UINT64  Base,
  IN UINT64  End
  )
{
  UINT64  Offset;
  UINT64  Limit;

  Limit = End - Base;
  if (Limit > (256ULL << 20)) {
    Limit = 256ULL << 20;
  }

  for (Offset = 0; (Offset + RAM_BLOCK_SIZE) <= Limit; Offset += RAM_BLOCK_SIZE) {
    if (IsFatBootSector ((CONST UINT8 *)(UINTN)(Base + Offset))) {
      return Base + Offset;
    }
  }

  return 0;
}

STATIC
VOID
PublishInitrd ('''

anchor_fn = 'STATIC\nVOID\nPublishInitrd ('
if anchor_fn not in source:
    raise SystemExit('cannot find PublishInitrd')
source = source.replace(anchor_fn, HELPERS, 1)

OLD = """  if ((End <= Start) || (((End - Start) % RAM_BLOCK_SIZE) != 0)) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd 0x%lx-0x%lx is not a whole number of sectors\\n", Start, End));
    return;
  }
"""

NEW = """  if (End <= Start) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd 0x%lx-0x%lx is empty\\n", Start, End));
    return;
  }

  // The bootloader may put its vendor ramdisk in front of the payload, and its range can end off a
  // sector boundary: clamp the end and publish from the FAT boot sector wherever it is.
  End &= ~(UINT64)(RAM_BLOCK_SIZE - 1);
  if (End <= Start) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd 0x%lx-0x%lx holds no whole sector\\n", Start, End));
    return;
  }

  {
    UINT64  Fat;

    Fat = FindFatStart (Start, End);
    if (Fat != 0) {
      DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: FAT boot sector at 0x%lx (+0x%lx of the initrd)\\n", Fat, Fat - Start));
      Start = Fat;
    } else {
      DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no FAT boot sector in initrd 0x%lx-0x%lx; keeping the range\\n", Start, End));
    }
  }
"""

if OLD not in source:
    raise SystemExit('cannot find the sector-alignment check')
source = source.replace(OLD, NEW, 1)

target.write_text(source)
print(f'patched {target}')
