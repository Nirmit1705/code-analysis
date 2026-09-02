// A relative import that points outside the ingested file set entirely —
// should fall back to an EXTERNAL edge keyed by its raw specifier text,
// not resolve to anything and not crash.
import { missing } from "../../../nonexistent";

export const orphan = missing;
