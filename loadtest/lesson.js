import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const INVITE = __ENV.TEACHER_INVITE_CODE;
const STUDENTS = Number(__ENV.STUDENTS || 200);
const ARRIVAL_SECONDS = Number(__ENV.ARRIVAL_SECONDS || 30);
const POLL_SECONDS = Number(__ENV.POLL_SECONDS || 120);
const ORDER_MODE = __ENV.ORDER_MODE || "join";
const PRESENCE = (__ENV.PRESENCE || "0") === "1";
const GRADE_INTERVAL_SECONDS = Number(__ENV.GRADE_INTERVAL_SECONDS || 15);
const POLL_INTERVAL_SECONDS = 5;
const TEACHER_VIEW_INTERVAL_SECONDS = 3;
const TEACHER_START_SECONDS = ARRIVAL_SECONDS + 5;
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
      startTime: "0s",
      duration: `${TOTAL_SECONDS}s`,
      exec: "teacher",
    },
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{name:join}": ["p(95)<1000"],
    "http_req_duration{name:me}": ["p(95)<500"],
    "http_req_duration{name:queue}": ["p(95)<1000"],
    "http_req_duration{name:grade}": ["p(95)<1000"],
    "http_req_duration{name:lottery}": ["p(95)<2000"],
  },
};

const json = { "Content-Type": "application/json" };

function report(name, response, allowed = [200]) {
  if (!allowed.includes(response.status)) {
    console.error(`${name} -> ${response.status} ${String(response.body).slice(0, 120)}`);
  }
}

export function setup() {
  const runId = Date.now().toString(36);
  const username = `ls_${runId}`;
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
  const session = http.post(
    `${BASE}/api/v1/sessions`,
    JSON.stringify({
      title: "Lesson load test",
      session_mode: "quick",
      order_mode: ORDER_MODE,
      presence_check: PRESENCE ? "rotating_code" : "off",
      presence_interval: 60,
    }),
    { headers: { ...json, ...auth }, tags: { name: "setup" } },
  );
  if (session.status !== 201) throw new Error(`session failed: ${session.status} ${session.body}`);
  return {
    runId,
    auth,
    code: session.json("code"),
    assignmentId: session.json("assignment_id"),
  };
}

function presenceCode(data) {
  const response = http.get(`${BASE}/api/v1/sessions/${data.code}/presence`, {
    headers: data.auth,
    tags: { name: "presence_code" },
  });
  return response.status === 200 ? response.json("code") : null;
}

export function student(data) {
  const token = `ls-${data.runId}-${__VU}`.padEnd(24, "x");
  sleep(Math.random() * ARRIVAL_SECONDS);

  const body = { name: `Student ${__VU}`, token };
  if (PRESENCE) body.presence_code = presenceCode(data);
  const joined = http.post(`${BASE}/api/v1/sessions/${data.code}/join`, JSON.stringify(body), {
    headers: json,
    tags: { name: "join" },
  });
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

let lotteryDone = false;
let criterionId = null;
let testStart = null;

export function teacher(data) {
  const headers = { ...json, ...data.auth };
  const base = `${BASE}/api/v1/sessions/${data.code}`;
  if (testStart === null) testStart = Date.now();
  const elapsed = () => (Date.now() - testStart) / 1000;

  if (!criterionId) {
    const criteria = http.get(`${BASE}/api/v1/assignments/${data.assignmentId}/criteria`, {
      headers,
      tags: { name: "setup" },
    });
    criterionId = criteria.json("0.id");
  }

  if (ORDER_MODE === "lottery" && !lotteryDone && elapsed() >= ARRIVAL_SECONDS + 2) {
    const lottery = http.post(`${base}/lottery`, null, { headers, tags: { name: "lottery" } });
    check(lottery, { "lottery 200": (r) => r.status === 200 });
    report("lottery", lottery);
    lotteryDone = lottery.status === 200;
  }

  if (elapsed() >= TEACHER_START_SECONDS) {
    const next = http.post(`${base}/next`, null, {
      headers,
      tags: { name: "next" },
      responseCallback: http.expectedStatuses(200, 409),
    });
    report("next", next, [200, 409]);
    if (next.status === 200) {
      const current = http.get(`${base}/submissions/current`, { headers, tags: { name: "current" } });
      report("current", current);
      if (current.status === 200) {
        const grade = http.post(
          `${BASE}/api/v1/submissions/${current.json("id")}/complete`,
          JSON.stringify({ scores: [{ criterion_id: criterionId, points: 7 }], comment: "" }),
          { headers, tags: { name: "grade" } },
        );
        check(grade, { "grade 200": (r) => r.status === 200 });
        report("grade", grade);
      }
    }
  }

  const cycleStart = Date.now();
  while (Date.now() - cycleStart < GRADE_INTERVAL_SECONDS * 1000) {
    sleep(TEACHER_VIEW_INTERVAL_SECONDS);
    const queue = http.get(`${base}/queue`, { headers, tags: { name: "queue" } });
    check(queue, { "queue 200": (r) => r.status === 200 });
    report("queue", queue);
    if (PRESENCE) {
      const code = http.get(`${base}/presence`, { headers, tags: { name: "presence_view" } });
      report("presence_view", code);
    }
    const budget = http.get(`${base}/budget`, { headers, tags: { name: "budget" } });
    report("budget", budget);
  }
}
