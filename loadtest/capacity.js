import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const INVITE = __ENV.TEACHER_INVITE_CODE;
const VUS = Number(__ENV.VUS || 64);
const DURATION = __ENV.DURATION || "20s";
const WAITING = Number(__ENV.WAITING || 200);

export const options = {
  scenarios: {
    hammer: { executor: "constant-vus", vus: VUS, duration: DURATION },
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

const json = { "Content-Type": "application/json" };

export function setup() {
  const runId = Date.now().toString(36);
  const username = `cap_${runId}`;
  const password = "capacity-password";
  http.post(
    `${BASE}/api/v1/auth/register`,
    JSON.stringify({ username, password, invite_code: INVITE }),
    { headers: json },
  );
  const login = http.post(`${BASE}/api/v1/auth/login`, JSON.stringify({ username, password }), {
    headers: json,
  });
  const auth = { Authorization: `Bearer ${login.json("access_token")}`, ...json };
  const code = http.post(`${BASE}/api/v1/sessions`, JSON.stringify({ title: "Capacity" }), {
    headers: auth,
  }).json("code");
  const tokens = [];
  for (let i = 0; i < WAITING; i++) {
    const token = `cap-${runId}-${i}`.padEnd(24, "x");
    http.post(`${BASE}/api/v1/sessions/${code}/join`, JSON.stringify({ name: `S${i}`, token }), {
      headers: json,
    });
    tokens.push(token);
  }
  return { code, tokens };
}

export default function (data) {
  const token = data.tokens[(__VU * 7919 + __ITER) % data.tokens.length];
  const response = http.get(`${BASE}/api/v1/sessions/${data.code}/me`, {
    headers: { "X-Entry-Token": token },
  });
  check(response, { "me 200": (r) => r.status === 200 });
}
