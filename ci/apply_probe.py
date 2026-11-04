#!/usr/bin/env python3
"""Add read-only probe logging to UniXNU Config.c.

Prints, at config load time, how many BlockIo / DiskIo / SimpleFileSystem
handles the firmware has after UniXNUConnectAll(), and the status of every
per-volume attempt to read \\EFI\\UniXNU\\unixnu.toml.

No behaviour changes: only UniXNULog calls are added.
"""
import sys
from pathlib import Path

INC_ANCHOR = '#include "Internal.h"\n'
INCLUDES = '\n#include <Protocol/BlockIo.h>\n#include <Protocol/DiskIo.h>\n'

ZERO_ANCHOR = "  ZeroMem (Config, sizeof (*Config));\n  Loaded = FALSE;\n"

PROBE = "".join([
    "  ZeroMem (Config, sizeof (*Config));\n",
    "  Loaded = FALSE;\n",
    "\n",
    "  {\n",
    "    EFI_HANDLE  *Probe;\n",
    "    UINTN       BlockCount;\n",
    "    UINTN       DiskCount;\n",
    "    UINTN       FsCount;\n",
    "\n",
    "    Probe = NULL;\n",
    "    if (EFI_ERROR (gBS->LocateHandleBuffer (ByProtocol, &gEfiBlockIoProtocolGuid, NULL, &BlockCount, &Probe))) {\n",
    "      BlockCount = 0;\n",
    "    } else {\n",
    "      FreePool (Probe);\n",
    "    }\n",
    "\n",
    "    Probe = NULL;\n",
    "    if (EFI_ERROR (gBS->LocateHandleBuffer (ByProtocol, &gEfiDiskIoProtocolGuid, NULL, &DiskCount, &Probe))) {\n",
    "      DiskCount = 0;\n",
    "    } else {\n",
    "      FreePool (Probe);\n",
    "    }\n",
    "\n",
    "    Probe = NULL;\n",
    "    if (EFI_ERROR (gBS->LocateHandleBuffer (ByProtocol, &gEfiSimpleFileSystemProtocolGuid, NULL, &FsCount, &Probe))) {\n",
    "      FsCount = 0;\n",
    "    } else {\n",
    "      FreePool (Probe);\n",
    "    }\n",
    "\n",
    '    UniXNULog ("probe: blockio %u diskio %u simplefs %u", (UINT32)BlockCount, (UINT32)DiskCount, (UINT32)FsCount);\n',
    "  }\n",
])

READ_ANCHOR = "".join([
    "  Status = UniXNUReadFile (Volume, (CONST CHAR16 *)PcdGetPtr (PcdUniXNUConfigPath), (VOID **)&Text, &Size);\n",
    "  if (EFI_ERROR (Status)) {\n",
    "    return FALSE;\n",
    "  }\n",
])

READ_PATCH = "".join([
    "  Status = UniXNUReadFile (Volume, (CONST CHAR16 *)PcdGetPtr (PcdUniXNUConfigPath), (VOID **)&Text, &Size);\n",
    "  if (EFI_ERROR (Status)) {\n",
    '    UniXNULog ("probe: volume 0x%lx read %r", (UINT64)(UINTN)Volume, Status);\n',
    "    return FALSE;\n",
    "  }\n",
])


def main():
    path = Path(sys.argv[1])
    text = path.read_text()
    if "probe: blockio" in text:
        print("already patched:", path)
        return
    for anchor, name in ((INC_ANCHOR, "include"), (ZERO_ANCHOR, "ZeroMem"), (READ_ANCHOR, "read")):
        if text.count(anchor) != 1:
            raise SystemExit("%s anchor count %d != 1" % (name, text.count(anchor)))
    text = text.replace(INC_ANCHOR, INC_ANCHOR + INCLUDES, 1)
    text = text.replace(ZERO_ANCHOR, PROBE, 1)
    text = text.replace(READ_ANCHOR, READ_PATCH, 1)
    path.write_text(text)
    print("patched:", path)


if __name__ == "__main__":
    main()
