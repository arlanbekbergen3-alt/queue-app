import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const INVITE = __ENV.TEACHER_INVITE_CODE;
const STUDENTS = Number(__ENV.STUDENTS || 300);
const ARRIVAL_SECONDS = Number(__ENV.ARRIVAL_SECONDS || 60);
const POLL_SECONDS = Number(__ENV.POLL_SECONDS || 240);
const POLL_INTERVAL_SECONDS = 5;
const TEACHER_INTERVAL_SECONDS = 60;
const TEACHER_VIEW_INTERVAL_SECONDS = 3;
const TEACHER_START_SECONDS = 15;
const TOTAL_SECONDS = ARRIVAL_SECONDS + POLL_SECONDS;

export const options = {
  scenarios: {
    students: {
      executor: "per-vu-iterations",
      vus: STUDENTS,
      iterations: 1,
      maxDuration: `${TOTAL_SECONDS + 120}s`,
      exec: "student",
    },
    teacher: {
      executor: "constant-vus",
      vus: 1,
      startTime: `${TEACHER_START_SECONDS}s`,
      duration: `${TOTAL_SECONDS - TEACHER_START_SECONDS}s`,
      exec: "teacher",
    },
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{name:join}": ["p(95)<1000"],
    "http_req_duration{name:me}": ["p(95)<500"],
    "http_req_duration{name:next}": ["p(95)<1000"],
    "http_req_duration{name:queue}": ["p(95)<1000"],
    "http_req_duration{name:done}": ["p(95)<1000"],
  },
};

const json = { "Content-Type": "application/json" };

function report(name, response) {
  if (response.status !== 200) {
    console.error(`${name} -> ${response.status} ${String(response.body).slice(0, 120)}`);
  }
}

export function setup() {
  const runId = Date.now().toString(36);
  const username = `lt_${runId}`;
  const password = "loadtest-password";
  const reg = http.post(
    `${BASE}/api/v1/auth/register`,
    JSON.stringify({ username, password, invite_code: INVITE }),
    { headers: json, tags: { name: "setup" } },
  );
  if (reg.status !== 201) throw new Error(`register failed: ${reg.status} ${reg.body}`);
  const login = http.post(`${BASE}/api/v1/auth/login`, JSON.stringify({ username, password }), {
    headers: json,
    tags: { name: "setup" },
  });
  const auth = { Authorization: `Bearer ${login.json("access_token")}` };
  const session = http.post(`${BASE}/api/v1/sessions`, JSON.stringify({ title: "Load test" }), {
    headers: { ...json, ...auth },
    tags: { name: "setup" },
  });
  return { runId, auth, code: session.json("code") };
}

export function student(data) {
  const token = `lt-${data.runId}-${__VU}`.padEnd(24, "x");
  sleep(Math.random() * ARRIVAL_SECONDS);

  const joined = http.post(
    `${BASE}/api/v1/sessions/${data.code}/join`,
    JSON.stringify({ name: `Student ${__VU}`, token }),
    { headers: json, tags: { name: "join" } },
  );
  check(joined, { "join 200": (r) => r.status === 200 });
  report("join", joined);

  const until = Date.now() + POLL_SECONDS * 1000;
  while (Date.now() < until) {
    sleep(POLL_INTERVAL_SECONDS + Math.random());
    const me = http.get(`${BASE}/api/v1/sessions/${data.code}/me`, {
      headers: { "X-Entry-Token": token },
      tags: { name: "me" },
    });
    check(me, { "me 200": (r) => r.status === 200 });
    report("me", me);
  }
}

let currentEntryId = null;

export function teacher(data) {
  const headers = { ...json, ...data.auth };
  const base = `${BASE}/api/v1/sessions/${data.code}`;
  const cycleStart = Date.now();

  if (currentEntryId) {
    const done = http.post(`${base}/entries/${currentEntryId}/done`, null, {
      headers,
      tags: { name: "done" },
    });
    check(done, { "done 200": (r) => r.status === 200 });
    report("done", done);
    currentEntryId = null;
  }
  const next = http.post(`${base}/next`, null, { headers, tags: { name: "next" } });
  report("next", next);
  if (next.status === 200) currentEntryId = next.json("id");

  while (Date.now() - cycleStart < TEACHER_INTERVAL_SECONDS * 1000) {
    sleep(TEACHER_VIEW_INTERVAL_SECONDS);
    const queue = http.get(`${base}/queue`, { headers, tags: { name: "queue" } });
    check(queue, { "queue 200": (r) => r.status === 200 });
    report("queue", queue);
  }
}
