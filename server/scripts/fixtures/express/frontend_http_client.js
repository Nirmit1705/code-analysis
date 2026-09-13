// Not Express. Structurally identical `.get(path, ...)`/`.post(path, ...)`
// shapes to a real Express route registration, but none of these
// identifiers are ever bound via `express()`/`express.Router()` — a
// regression fixture for the false positive found during Phase 6.5's real
// dogfooding against Nirmit1705/HireSight (see server/CLAUDE.md). None of
// these calls should ever be recorded as a route.
import axios from "axios";

const apiClient = axios.create({ baseURL: "/api" });

export function fetchToken() {
  return apiClient.get("/token");
}

export function startAptitude(payload) {
  return apiClient.post("/aptitude/start", payload);
}

export function getAptitudeHistory() {
  return apiClient.get("/aptitude/history");
}

// The bare, no-factory-call shape.
axios.get("/not-a-route-either");

// The single most realistic near-miss: a variable literally named
// `router`, matching Express's own naming convention, that has nothing to
// do with Express at all — must not be mistaken for one merely by
// variable name.
const router = someRoutingLibrary.createRouter();
router.get("/also-not-a-route", handler);
