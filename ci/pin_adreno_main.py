# AdrenoMetal pin removed: keep the revision from the pinned components.toml
# The previous version forced AdrenoMetal to the latest main revision, which
# violated the rule "do not switch to latest main without a controlled diff".
import sys
print('AdrenoMetal pin disabled: keeping pinned revision from components.toml')
