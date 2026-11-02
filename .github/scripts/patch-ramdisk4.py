#!/usr/bin/env python3
"""Make UniXNURamDiskDxe report every decision on the platform log and the console.

The sheng platform routes DEBUG output into the display-reserved region
(ShengGopLogDxe publishes the scan-out buffer, FrameBufferSerialPortLib draws the text, and
sheng.dsc sets PcdFrameBufferFontScale/PcdFrameBufferDelay), so DEBUG is the channel that
actually reaches the tablet's screen. ConOut is kept as a second channel for firmwares that
have a text console.

The driver also searches the initrd for the FAT boot sector instead of assuming it starts at
linux,initrd-start, and falls back to the FDT reserve map when /chosen carries no initrd.
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path('UniXNU')
target = ROOT / 'package/UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.c'
source = target.read_text()

if 'UNIXNURAMDISK: report' in source:
    print('already patched')
    raise SystemExit(0)

# --- helpers injected before PublishInitrd -------------------------------------------------
HELPERS = r'''
// Report through both channels the platform may have: the DEBUG log, which sheng draws into the
// display-reserved frame buffer, and the text console when one exists.
STATIC
VOID
Report (
  IN CONST CHAR8  *Format,
  IN UINT64       Value,
  IN UINT64       Extra
  )
{
  CHAR16  Wide[160];
  UINTN   Index;

  DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: "));
  DEBUG ((DEBUG_ERROR, Format, Value, Extra));
  DEBUG ((DEBUG_ERROR, "\n"));

  if ((gST == NULL) || (gST->ConOut == NULL)) {
    return;
  }

  for (Index = 0; (Format[Index] != '\0') && (Index < 150); Index++) {
    Wide[Index] = (CHAR16)Format[Index];
  }
  Wide[Index] = L'\0';
  gST->ConOut->OutputString (gST->ConOut, L"UNIXNURAMDISK: ");
  gST->ConOut->OutputString (gST->ConOut, Wide);
  gST->ConOut->OutputString (gST->ConOut, L"\r\n");
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

  // The loader's vendor ramdisk does not end on a sector boundary, so check every even offset.
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
source = source.replace(anchor, HELPERS, 1)

# --- entry: report the HOB and the device tree --------------------------------------------
source = source.replace(
    '''  Hob = GetFirstGuidHob (&mEntryArgHobGuid);
  if (Hob == NULL) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no entry argument HOB\\n"));
    return EFI_SUCCESS;
  }''',
    '''  Hob = GetFirstGuidHob (&mEntryArgHobGuid);
  if (Hob == NULL) {
    Report ("report 1: no entry argument HOB, driver stops here\\n", 0, 0);
    return EFI_SUCCESS;
  }''', 1)

source = source.replace(
    '''  if ((Dtb == 0) || ((Dtb & 3) != 0) || (FdtCheckHeader ((VOID *)(UINTN)Dtb) != 0)) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: x0 0x%lx is not a device tree\\n", Dtb));
    return EFI_SUCCESS;
  }''',
    '''  if ((Dtb == 0) || ((Dtb & 3) != 0) || (FdtCheckHeader ((VOID *)(UINTN)Dtb) != 0)) {
    Report ("report 2: HOB x0=%lx is not a device tree, driver stops here\\n", Dtb, 0);
    return EFI_SUCCESS;
  }''', 1)

source = source.replace(
    '''  DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: device tree at 0x%lx, %u bytes\\n", Dtb, (UINT32)FdtTotalSize ((VOID *)(UINTN)Dtb)));''',
    '''  Report ("report 3: device tree at %lx size %lx\\n", Dtb, FdtTotalSize ((VOID *)(UINTN)Dtb));''', 1)

# --- /chosen handling ---------------------------------------------------------------------
source = source.replace(
    '''  Chosen = FdtPathOffset (Fdt, "/chosen");
  if (Chosen < 0) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: no /chosen\\n"));
    return;
  }''',
    '''  Chosen = FdtPathOffset (Fdt, "/chosen");
  Report ("report 4: /chosen offset %lx\\n", (UINT64)(UINT32)Chosen, 0);
  if (Chosen < 0) {
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
    INTN  Index;
    INTN  Count;

    Report ("report 5: /chosen has NO linux,initrd-start/end\\n", 0, 0);
    Count = FdtGetNumberOfReserveMapEntries (Fdt);
    Report ("report 6: reserve map entries %lx\\n", (UINT64)Count, 0);
    for (Index = 0; Index < Count; Index++) {
      UINT64  RsvStart;
      UINT64  RsvSize;
      UINT64  RsvEnd;
      UINT64  Fat;

      if (FdtGetReserveMapEntry (Fdt, Index, &RsvStart, &RsvSize) != 0) {
        break;
      }

      RsvEnd = RsvStart + RsvSize;
      Report ("report 7: reserve %lx-%lx\\n", RsvStart, RsvEnd);
      if (RsvEnd <= RsvStart) {
        continue;
      }

      Fat = FindFatStart (RsvStart, RsvEnd & ~(UINT64)(RAM_BLOCK_SIZE - 1));
      if (Fat != 0) {
        Report ("report 8: FAT in reserve at %lx\\n", Fat, 0);
        Start = Fat;
        End   = RsvEnd & ~(UINT64)(RAM_BLOCK_SIZE - 1);
        goto Publish;
      }
    }

    return;
  }''', 1)

# --- align, search and report the chosen range --------------------------------------------
source = source.replace(
    '''  if ((End <= Start) || (((End - Start) % RAM_BLOCK_SIZE) != 0)) {
    DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd 0x%lx-0x%lx is not a whole number of sectors\\n", Start, End));
    return;
  }''',
    '''  Report ("report 9: chosen initrd %lx-%lx\\n", Start, End);

  End &= ~(UINT64)(RAM_BLOCK_SIZE - 1);
  if (End <= Start) {
    Report ("report 10: chosen range holds no whole sector\\n", Start, End);
    return;
  }

  {
    UINT64  Fat;

    Fat = FindFatStart (Start, End);
    if (Fat != 0) {
      Report ("report 11: FAT at initrd offset %lx\\n", Fat - Start, 0);
      Start = Fat;
    } else {
      Report ("report 12: no FAT boot sector in the initrd\\n", Start, End);
    }
  }

Publish:''', 1)

source = source.replace(
    '''  DEBUG ((DEBUG_ERROR, "UNIXNURAMDISK: initrd block device 0x%lx size 0x%lx %r\\n", Start, End - Start, Status));''',
    '''  Report ("report 13: block device %lx size %lx\\n", Start, End - Start);
  Report ("report 14: install status %lx\\n", (UINT64)Status, 0);''', 1)

target.write_text(source)
print(f'patched {target}')
