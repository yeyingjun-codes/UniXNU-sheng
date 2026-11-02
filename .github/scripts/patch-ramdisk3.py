#!/usr/bin/env python3
"""Make UniXNURamDiskDxe report on the framebuffer console and prefer the FDT reserve map.

Upstream behaviour, traced in the source:

 * the driver reads /chosen/linux,initrd-start and linux,initrd-end through the entry-argument
   HOB, and publishes exactly that range as a read-only Block I/O device;
 * every message goes through DEBUG(), which a RELEASE/DEBUG EDK II build strips from the
   binary unless a debug port is present, so a device without a serial line shows nothing.

This patch keeps the upstream range, aligns its end down to a sector, searches the range for a
FAT boot sector, and prints one line with gST->ConOut so the result is visible on the tablet.
It changes no other driver and no board file.
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path('UniXNU')
target = ROOT / 'package/UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.c'
source = target.read_text()

if 'Say (L"' in source:
    print('already patched')
    raise SystemExit(0)

# Console line helper, before PublishInitrd.
HELPER = '''
STATIC
VOID
Say (
  IN CONST CHAR16  *Text,
  IN UINT64        A,
  IN UINT64        B
  )
{
  if ((gST != NULL) && (gST->ConOut != NULL)) {
    gST->ConOut->OutputString (gST->ConOut, (CHAR16 *)Text);
    if (A != 0) {
      gST->ConOut->OutputString (gST->ConOut, L"0x");
      {
        CHAR16  Buffer[17];
        UINTN   Index;
        for (Index = 0; Index < 16; Index++) {
          Buffer[15 - Index] = L"0123456789abcdef"[(A >> (Index * 4)) & 0xF];
        }
        Buffer[16] = L'\\0';
        gST->ConOut->OutputString (gST->ConOut, Buffer);
      }
    }
    if (B != 0) {
      gST->ConOut->OutputString (gST->ConOut, L" 0x");
      {
        CHAR16  Buffer[17];
        UINTN   Index;
        for (Index = 0; Index < 16; Index++) {
          Buffer[15 - Index] = L"0123456789abcdef"[(B >> (Index * 4)) & 0xF];
        }
        Buffer[16] = L'\\0';
        gST->ConOut->OutputString (gST->ConOut, Buffer);
      }
    }
    gST->ConOut->OutputString (gST->ConOut, L"\\r\\n");
  }
}

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

  return (CompareMem (Sector + 54, "FAT12   ", 8) == 0) ||
         (CompareMem (Sector + 54, "FAT16   ", 8) == 0) ||
         (CompareMem (Sector + 82, "FAT32   ", 8) == 0);
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

  for (Offset = 0; (Offset + RAM_BLOCK_SIZE) <= Limit; Offset += 2) {
    if (IsFatBootSector ((CONST UINT8 *)(UINTN)(Base + Offset))) {
      return Base + Offset;
    }
  }

  return 0;
}

STATIC
VOID
PublishInitrd ('''

anchor = 'STATIC\nVOID\nPublishInitrd ('
if anchor not in source:
    raise SystemExit('cannot find PublishInitrd')
source = source.replace(anchor, HELPER, 1)

# Report the entry argument and the parsed range.
source = source.replace(
    '''  Hob = GetFirstGuidHob (&mEntryArgHobGuid);
  if (Hob == NULL) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no entry argument HOB\\n"));
    return EFI_SUCCESS;
  }''',
    '''  Hob = GetFirstGuidHob (&mEntryArgHobGuid);
  if (Hob == NULL) {
    Say (L"UNIXNURAMDISK: no entry argument HOB", 0, 0);
    return EFI_SUCCESS;
  }''', 1)

source = source.replace(
    '''  if ((Dtb == 0) || ((Dtb & 3) != 0) || (FdtCheckHeader ((VOID *)(UINTN)Dtb) != 0)) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: x0 0x%lx is not a device tree\\n", Dtb));
    return EFI_SUCCESS;
  }''',
    '''  if ((Dtb == 0) || ((Dtb & 3) != 0) || (FdtCheckHeader ((VOID *)(UINTN)Dtb) != 0)) {
    Say (L"UNIXNURAMDISK: x0 is not a device tree:", Dtb, 0);
    return EFI_SUCCESS;
  }''', 1)

source = source.replace(
    '''  DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: device tree at 0x%lx, %u bytes\\n", Dtb, (UINT32)FdtTotalSize ((VOID *)(UINTN)Dtb)));''',
    '''  Say (L"UNIXNURAMDISK: device tree, bytes:", Dtb, FdtTotalSize ((VOID *)(UINTN)Dtb));''', 1)

# /chosen branch reporting and the FAT search.
source = source.replace(
    '''  Chosen = FdtPathOffset (Fdt, "/chosen");
  if (Chosen < 0) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no /chosen\\n"));
    return;
  }''',
    '''  Chosen = FdtPathOffset (Fdt, "/chosen");
  if (Chosen < 0) {
    Say (L"UNIXNURAMDISK: no /chosen in the device tree", 0, 0);
    return;
  }''', 1)

source = source.replace(
    '''  if ((StartProp == NULL) || (EndProp == NULL) ||
      ((StartLength != 4) && (StartLength != 8)) || ((EndLength != 4) && (EndLength != 8)))
  {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no initrd in /chosen\\n"));
    return;
  }''',
    '''  if ((StartProp == NULL) || (EndProp == NULL) ||
      ((StartLength != 4) && (StartLength != 8)) || ((EndLength != 4) && (EndLength != 8)))
  {
    Say (L"UNIXNURAMDISK: /chosen has NO linux,initrd-start/end", 0, 0);
    Say (L"UNIXNURAMDISK: reserve-map entries:", (UINT64)FdtGetNumberOfReserveMapEntries (Fdt), 0);
    {
      INTN  Index;
      INTN  Count;

      Count = FdtGetNumberOfReserveMapEntries (Fdt);
      for (Index = 0; Index < Count; Index++) {
        UINT64  RsvStart;
        UINT64  RsvSize;
        UINT64  RsvEnd;
        UINT64  Fat;

        if (FdtGetReserveMapEntry (Fdt, Index, &RsvStart, &RsvSize) != 0) {
          break;
        }

        RsvEnd = RsvStart + RsvSize;
        Say (L"UNIXNURAMDISK: reserve", RsvStart, RsvEnd);
        if (RsvEnd <= RsvStart) {
          continue;
        }

        Fat = FindFatStart (RsvStart, RsvEnd & ~(UINT64)(RAM_BLOCK_SIZE - 1));
        if (Fat != 0) {
          Say (L"UNIXNURAMDISK: FAT in reserve at", Fat, 0);
          Start = Fat;
          End   = RsvEnd & ~(UINT64)(RAM_BLOCK_SIZE - 1);
          goto Publish;
        }
      }
    }

    return;
  }''', 1)

# Align, search, and report the chosen range.
source = source.replace(
    '''  if ((End <= Start) || (((End - Start) % RAM_BLOCK_SIZE) != 0)) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd 0x%lx-0x%lx is not a whole number of sectors\\n", Start, End));
    return;
  }''',
    '''  Say (L"UNIXNURAMDISK: chosen initrd", Start, End);

  End &= ~(UINT64)(RAM_BLOCK_SIZE - 1);
  if (End <= Start) {
    Say (L"UNIXNURAMDISK: chosen initrd holds no whole sector", Start, End);
    return;
  }

  {
    UINT64  Fat;

    Fat = FindFatStart (Start, End);
    if (Fat != 0) {
      Say (L"UNIXNURAMDISK: FAT at initrd offset", Fat - Start, 0);
      Start = Fat;
    } else {
      Say (L"UNIXNURAMDISK: no FAT boot sector in the initrd", Start, End);
    }
  }

Publish:''', 1)

source = source.replace(
    '''  DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd block device 0x%lx size 0x%lx %r\\n", Start, End - Start, Status));''',
    '''  Say (L"UNIXNURAMDISK: block device published, size:", Start, End - Start);
  Say (L"UNIXNURAMDISK: install status:", Status, 0);''', 1)

target.write_text(source)
print(f'patched {target}')
