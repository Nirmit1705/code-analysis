/** Adds two numbers. */
export function add(a: number, b: number): number {
  return a + b;
}

const multiply = (a: number, b: number): number => a * b;

export const divide = (a: number, b: number): number => {
  return a / b;
};

interface Point {
  x: number;
  y: number;
}

type Pair = [number, number];
