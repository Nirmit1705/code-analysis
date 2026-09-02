// Two levels of "../" from a non-root-level file — regression check for
// correct ".." collapsing (see imports.py's module docstring on the gap
// this exercises).
import { helperFn } from "../../utils/helpers";

export const deep = helperFn();
