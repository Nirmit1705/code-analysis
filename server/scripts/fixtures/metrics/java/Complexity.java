// Fixture for smoke_metrics.py's Java cyclomatic-complexity checks. Each
// method's expected complexity is noted above it; see the smoke script for
// the exact assertions.

public class Complexity {

    public int noBranches() {
        return 1;
        // complexity 1 (no decision points)
    }

    public int withIf(int a) {
        if (a > 0) {
            return 1;
        }
        return 0;
        // complexity 2 (1 if_statement)
    }

    public int withIfElseIf(int a, int b) {
        if (a > 0) {
            return 1;
        } else if (b > 0) {
            return 2;
        } else {
            return 3;
        }
        // complexity 3 (2 if_statement: outer + the else-if's nested one)
    }

    public int withLoops(int[] items) {
        for (int i = 0; i < items.length; i++) {
            if (items[i] < 0) {
                continue;
            }
        }
        for (int x : items) {
            System.out.println(x);
        }
        int i = 0;
        while (i < 10) {
            i++;
        }
        do {
            i--;
        } while (i > 0);
        return i;
        // complexity 6 (for + if + enhanced-for + while + do-while = 5 decision points)
    }

    public int withTryCatch() {
        try {
            riskyThing();
        } catch (Exception e) {
            return -1;
        }
        return 1;
        // complexity 2 (1 catch_clause)
    }

    public String withSwitch(int x) {
        switch (x) {
            case 1:
                return "a";
            case 2:
                return "b";
            default:
                return "c";
        }
        // complexity 3 (2 case labels; the default label doesn't count)
    }

    public int withTernary(boolean a) {
        return a ? 1 : 2;
        // complexity 2 (1 ternary_expression)
    }

    public boolean withLogicalAnd(boolean a, boolean b) {
        return a && b;
        // complexity 2 (1 binary_expression with &&)
    }

    public boolean withLogicalOr(boolean a, boolean b) {
        return a || b;
        // complexity 2 (1 binary_expression with ||)
    }

    private void riskyThing() throws Exception {
    }
}
