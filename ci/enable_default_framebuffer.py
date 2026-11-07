from pathlib import Path
import json
import sys

source_path = Path(sys.argv[1])
abi_path = Path(sys.argv[2])
text = source_path.read_text()
anchor = """    IOReturn newUserClient(task_t task,void *security,UInt32 type,IOUserClient **client) override {
"""
override = """    void create_default_fb_surface() override {
        // Keep Apple's implementation first. If its internal creation gate is
        // closed, use the reviewed owned factory instead of forcing that gate.
        IOMobileFramebufferLegacy::create_default_fb_surface();
        uint32_t width=0,height=0;
        getDisplayDimensions(&width,&height);
        installDefaultSurface(width,height);
    }
"""
if text.count(anchor) != 1:
    raise SystemExit("expected exactly one newUserClient anchor")
source_path.write_text(text.replace(anchor, override + anchor))

abi = json.loads(abi_path.read_text())
if abi["display"]["default_surface"] not in (7000, 7008):
    raise SystemExit("unexpected default_surface offset")
# Original create_default_fb_surface stores the allocated object at 0x1b60;
# stop releases and clears the same field. The previous 7000 was inconsistent.
abi["display"]["default_surface"] = 7008
abi_path.write_text(json.dumps(abi, indent=2) + "\n")
print("installed the owned default-surface fallback at the binary-verified offset 7008")
