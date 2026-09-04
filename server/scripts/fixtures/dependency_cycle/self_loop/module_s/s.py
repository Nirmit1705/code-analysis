# A file whose own absolute import resolves back to itself (suffix-matches
# "module_s/s.py", the only file with that suffix) — a deliberate 1-node
# self-loop, regression fixture for graph.py's is_self_loop cycle path.
from module_s.s import helper


def helper():
    return "self"
