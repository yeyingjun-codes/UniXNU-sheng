from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = 'revision = "243d63cf43305a85c8119b66b49cb7df4b30b019"'
new = 'revision = "6d7f73437087c57ee54a41adceb1735daaea405e"'
if text.count(old) != 1:
    raise SystemExit('expected exactly one AdrenoMetal revision')
path.write_text(text.replace(old, new))
print('upgraded AdrenoMetal revision in', path)
