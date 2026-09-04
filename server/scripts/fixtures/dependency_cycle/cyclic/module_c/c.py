from module_a.a import thing_a
# A second, separate import statement targeting the exact same file as the
# line above (mirroring TypeScript's routine `import type {X} from './y'` +
# `import {z} from './y'` split) — regression fixture for graph.py's
# coupling dedup: this must NOT double-count as two module_c -> module_a
# dependencies.
from module_a.a import thing_a as thing_a_alias


def use_it():
    return thing_a() or thing_a_alias()
