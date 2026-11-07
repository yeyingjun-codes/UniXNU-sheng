from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = "        OSSafeReleaseNULL(mapping);return valid;"
new = """        OSSafeReleaseNULL(mapping);
        if(valid) {
            // The original 24A446 create_default_fb_surface() gates on bit 0
            // at object+0x1cc0. The original startHardware slot is a false stub,
            // so the owned hardware backend must publish this state after a
            // validated GOP mapping. Apple retains the actual surface factory.
            unsigned char *state=reinterpret_cast<unsigned char *>(this);
            uint8_t flags=0;
            memcpy(&flags,state+0x1cc0,1);
            flags|=1;
            memcpy(state+0x1cc0,&flags,1);
        }
        return valid;"""
if text.count(old) != 1:
    raise SystemExit("expected exactly one startHardware return")
path.write_text(text.replace(old, new))
print("enabled the original default framebuffer creation gate after GOP mapping")
