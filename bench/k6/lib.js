// Shared by the four scenarios of the products reads benchmark (ADR-0042).
//
// Environment (all set by bench/run.sh):
//   BASE_URL    the build under test, e.g. http://127.0.0.1:5003
//   TOKEN       an RS256 access token whose session is live in redis-state
//   FORMAT      json | xml
//   MODE        calibrate | measure
//   RATES       calibrate: comma-separated arrival rates (requests/s), one step each
//   STEP_SECONDS  calibrate: seconds per step
//   RATE        measure: the constant arrival rate (requests/s)
//   WARMUP_SECONDS, MEASURE_SECONDS   measure: the two phases
//   SUMMARY_PATH  where the raw summary JSON is written
import http from "k6/http";
import { check } from "k6";

const FORMATS = {
  json: { accept: "application/json", type: "application/json", first: "{" },
  xml: { accept: "application/xml", type: "application/xml", first: "<" },
};

const format = FORMATS[__ENV.FORMAT || "json"];
const mode = __ENV.MODE || "measure";

function integer(name, fallback) {
  const value = parseInt(__ENV[name] || "", 10);
  return Number.isFinite(value) ? value : fallback;
}

function range(from, to) {
  return from + Math.floor(Math.random() * (to - from + 1));
}

export function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

export { range };

// One request: GET `path` with the build's Accept, expecting `expected`
// statuses (200, and 404 for the detail scenario's unknown ids).
export function get(path, expected) {
  const response = http.get(`${__ENV.BASE_URL}${path}`, {
    headers: {
      Accept: format.accept,
      Authorization: `Bearer ${__ENV.TOKEN}`,
    },
    responseCallback: http.expectedStatuses(...expected),
  });
  check(response, {
    status: (r) => expected.includes(r.status),
    format: (r) =>
      String(r.headers["Content-Type"] || "").startsWith(format.type) &&
      typeof r.body === "string" &&
      r.body.charAt(0) === format.first,
  });
  return response;
}

function arrival(rate, duration, startTime, extra) {
  return Object.assign(
    {
      executor: "constant-arrival-rate",
      rate,
      timeUnit: "1s",
      duration,
      startTime,
      preAllocatedVUs: Math.max(20, Math.ceil(rate / 10)),
      maxVUs: 600,
    },
    extra || {},
  );
}

// Submetrics only reach the summary when a threshold names them, so each
// phase gets thresholds that cannot fail.
function observe(scenarios) {
  const thresholds = {};
  for (const name of scenarios) {
    thresholds[`http_req_duration{scenario:${name}}`] = ["max>=0"];
    thresholds[`http_req_failed{scenario:${name}}`] = ["rate>=0"];
    thresholds[`http_reqs{scenario:${name}}`] = ["count>=0"];
    thresholds[`dropped_iterations{scenario:${name}}`] = ["count>=0"];
    thresholds[`checks{scenario:${name}}`] = ["rate>=0"];
  }
  return thresholds;
}

export function buildOptions() {
  const scenarios = {};
  if (mode === "calibrate") {
    // A ramp of constant-arrival steps, one scenario per step, so the summary
    // holds each step's latency, errors and dropped iterations on their own.
    const step = integer("STEP_SECONDS", 10);
    (__ENV.RATES || "10,20,40").split(",").forEach((rate, i) => {
      scenarios[`step${i}`] = arrival(
        parseInt(rate, 10),
        `${step}s`,
        `${i * step}s`,
        { tags: { rate } },
      );
    });
  } else {
    const warmup = integer("WARMUP_SECONDS", 30);
    const measure = integer("MEASURE_SECONDS", 120);
    const rate = integer("RATE", 10);
    scenarios.warmup = arrival(rate, `${warmup}s`, "0s");
    scenarios.measure = arrival(rate, `${measure}s`, `${warmup}s`);
  }
  return {
    scenarios,
    thresholds: observe(Object.keys(scenarios)),
    summaryTrendStats: ["min", "med", "p(90)", "p(95)", "p(99)", "max", "avg"],
    discardResponseBodies: false,
    noConnectionReuse: false,
  };
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY_PATH || "/out/summary.json"]: JSON.stringify(data) };
}
