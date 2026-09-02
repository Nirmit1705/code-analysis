import React from "react";
import { helperFn } from "../utils/helpers";
import { other } from "@/utils/helpers";
import pkg from "@scope/pkg";
const local = require("./local");

export function Widget() {
  import("./lazy");
  return React.createElement("div", null, helperFn() + other + pkg + local);
}
