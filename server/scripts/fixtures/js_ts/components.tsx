import React from "react";

/** Renders a greeting. */
export function Greeting({ name }: { name: string }) {
  if (!name) {
    return <span>Hello, world!</span>;
  }
  return <span>Hello, {name}!</span>;
}

export const Arrow = () => {
  return <div>arrow</div>;
};

export const Implicit = () => <div>implicit</div>;

export class Widget extends React.Component {
  render() {
    return <div>widget</div>;
  }
}

export class PlainHelper {
  helper() {
    return 1;
  }
}

function useCount() {
  return 1;
}
