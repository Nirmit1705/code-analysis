// Fixture for smoke_metrics.py's JS/TS cyclomatic-complexity checks.
// Each function's expected complexity is noted above it; see the smoke
// script for the exact assertions.

export function noBranches() {
  return 1;
}
// complexity 1 (no decision points)

export function withIf(a: number) {
  if (a > 0) {
    return 1;
  }
  return 0;
}
// complexity 2 (1 if_statement)

export function withIfElseIf(a: number, b: number) {
  if (a > 0) {
    return 1;
  } else if (b > 0) {
    return 2;
  } else {
    return 3;
  }
}
// complexity 3 (2 if_statement: the outer one + the else-if's nested one; the
// final plain `else` isn't itself a decision point)

export function withLoops(items: number[]) {
  for (const x of items) {
    if (x < 0) {
      continue;
    }
  }
  for (const key in { a: 1 }) {
    console.log(key);
  }
  let i = 0;
  while (i < 10) {
    i++;
  }
  do {
    i--;
  } while (i > 0);
  return i;
}
// complexity 6 (for-of + if + for-in + while + do-while = 5 decision points)

export function withTryCatch() {
  try {
    riskyThing();
  } catch (e) {
    return null;
  }
  return 1;
}
// complexity 2 (1 catch_clause)

export function withSwitch(x: number): string {
  switch (x) {
    case 1:
      return "a";
    case 2:
      return "b";
    default:
      return "c";
  }
}
// complexity 3 (2 switch_case; switch_default doesn't count)

export function withTernary(a: boolean) {
  return a ? 1 : 2;
}
// complexity 2

export function withLogicalAnd(a: boolean, b: boolean) {
  return a && b;
}
// complexity 2

export function withLogicalOr(a: boolean, b: boolean) {
  return a || b;
}
// complexity 2

export function withNullishCoalescing(a: number | null, b: number) {
  return a ?? b;
}
// complexity 2

export function withNestedFunction(a: number) {
  const helper = (b: number) => {
    if (b > 0) {
      return 1;
    }
    return 0;
  };
  if (a > 0) {
    return helper(a);
  }
  return 0;
}
// outer withNestedFunction: complexity 2 (1 if — the helper's own `if` must
// NOT be counted here, since it's a separate nested function)
// helper: complexity 2 (its own 1 if)

export class Widget {
  render(active: boolean) {
    if (active) {
      return "on";
    }
    return "off";
  }
}
// method render: complexity 2

function riskyThing(): void {}
