# Fixture for smoke_metrics.py's Python cyclomatic-complexity checks.
# Each function's expected complexity is noted above it; see the smoke
# script for the exact assertions.


def no_branches():
    return 1
    # complexity 1 (no decision points)


def with_if(a):
    if a > 0:
        return 1
    return 0
    # complexity 2 (1 if_statement)


def with_if_elif_else(a, b):
    if a > 0:
        return 1
    elif b > 0:
        return 2
    else:
        return 3
    # complexity 3 (1 if_statement + 1 elif_clause; plain `else` doesn't count)


def with_loops(items):
    for x in items:
        if x < 0:
            continue
    i = 0
    while i < 10:
        i += 1
    return i
    # complexity 4 (for_statement + if_statement + while_statement)


def with_try_except():
    try:
        risky_thing()
    except ValueError:
        return None
    except TypeError:
        return None
    return 1
    # complexity 3 (2 except_clause)


def with_match(x):
    match x:
        case 1:
            return "a"
        case 2:
            return "b"
        case _:
            return "c"
    # complexity 4 (3 case_clause — Python's wildcard `case _:` has no
    # distinct "default" node type, so it counts the same as any other case)


def with_ternary(a):
    return 1 if a else 2
    # complexity 2 (1 conditional_expression)


def with_boolean_and(a, b):
    return a and b
    # complexity 2 (1 boolean_operator)


def with_boolean_or(a, b):
    return a or b
    # complexity 2 (1 boolean_operator)


def with_comprehension_filter(items):
    return [x for x in items if x > 0]
    # complexity 2 (1 if_clause)


def with_nested_function(a):
    def helper(b):
        if b > 0:
            return 1
        return 0

    if a > 0:
        return helper(a)
    return 0
    # outer with_nested_function: complexity 2 (1 if_statement — helper's own
    # `if` must NOT be counted here, since it's a separate nested function)
    # helper: complexity 2 (its own 1 if_statement)


class Widget:
    def render(self, active):
        if active:
            return "on"
        return "off"
        # method render: complexity 2


def risky_thing():
    pass
