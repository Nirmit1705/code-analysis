/** A simple counter. */
export class Counter {
  private count: number;

  constructor() {
    this.count = 0;
  }

  /** Increments the counter. */
  increment(): number {
    this.count += 1;
    return this.count;
  }

  static zero(): Counter {
    return new Counter();
  }

  reset = (): void => {
    this.count = 0;
  };
}

abstract class Shape {
  abstract area(): number;
}
