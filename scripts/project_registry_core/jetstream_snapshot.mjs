import { Jetstream } from "@bsky/jetstream";

const input = await new Promise((resolve, reject) => {
  let body = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { body += chunk; });
  process.stdin.on("end", () => {
    try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
  });
  process.stdin.on("error", reject);
});

const service = String(input.service || "").replace(/\/$/, "");
const collection = String(input.collection || "");
const afterSeq = Math.max(0, Number(input.after_seq || 0));
const maxEvents = Math.max(1, Number(input.max_events || 10000));
const maxSeconds = Math.max(0, Number(input.max_seconds || 0));
const apiKey = String(process.env.ATPROTO_SURVEY_JETSTREAM_API_KEY || "").trim();
if (!service || !collection) throw new Error("Jetstream service and collection are required");
if (!apiKey) throw new Error("ATPROTO_SURVEY_JETSTREAM_API_KEY is not configured");

const controller = new AbortController();
let timedOut = false;
const timeout = maxSeconds > 0 ? setTimeout(() => {
  timedOut = true;
  controller.abort();
}, maxSeconds * 1000) : null;
timeout?.unref();
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => controller.abort());
}

function write(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function sealedTipSeq() {
  const response = await fetch(`${service}/xrpc/network.bsky.jetstream.planSnapshot`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({collections: [collection], kinds: ["commit"], afterSeq}),
    signal: controller.signal,
  });
  if (!response.ok) throw new Error(`Jetstream snapshot planning failed with HTTP ${response.status}`);
  const payload = await response.json();
  const tip = Number(payload.sealedTipSeq || 0);
  if (!Number.isSafeInteger(tip) || tip < afterSeq) throw new Error("Jetstream returned an invalid sealed tip sequence");
  return tip;
}

const beforeSeq = await sealedTipSeq();
if (beforeSeq <= afterSeq) {
  write({
    type: "summary",
    after_seq: afterSeq,
    sealed_tip_seq: beforeSeq,
    last_event_seq: afterSeq,
    event_count: 0,
    truncated: false,
    cancelled: false,
  });
} else {
  const jetstream = new Jetstream({
    service,
    apiKey,
    retry: {maxAttempts: 4, baseDelayMs: 250, maxDelayMs: 5000},
    blockConcurrency: 4,
    snapshotBufferBytes: 32 * 1024 * 1024,
  });
  let eventCount = 0;
  let lastEventSeq = afterSeq;
  let truncated = false;

  try {
    for await (const event of jetstream.snapshot({
      collections: [collection],
      kinds: ["commit"],
      afterSeq,
      beforeSeq,
      signal: controller.signal,
    })) {
      if (event.kind !== "commit" || event.commit.collection !== collection) continue;
      eventCount += 1;
      lastEventSeq = Number(event.seq);
      write({
        type: "event",
        seq: event.seq,
        time: event.time,
        did: event.did,
        operation: event.commit.operation,
        rev: event.commit.rev,
        collection: event.commit.collection,
        rkey: event.commit.rkey,
        record: event.commit.operation === "delete" ? null : event.commit.record,
      });
      if (eventCount >= maxEvents) {
        truncated = lastEventSeq < beforeSeq;
        break;
      }
    }
    truncated = truncated || controller.signal.aborted;
    write({
      type: "summary",
      after_seq: afterSeq,
      sealed_tip_seq: beforeSeq,
      last_event_seq: lastEventSeq,
      event_count: eventCount,
      truncated,
      cancelled: controller.signal.aborted,
      timed_out: timedOut,
    });
  } catch (error) {
    if (!controller.signal.aborted && eventCount === 0) throw error;
    const message = String(error?.message || error || "snapshot interrupted")
      .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
      .slice(0, 300);
    write({
      type: "summary",
      after_seq: afterSeq,
      sealed_tip_seq: beforeSeq,
      last_event_seq: lastEventSeq,
      event_count: eventCount,
      truncated: true,
      cancelled: controller.signal.aborted,
      timed_out: timedOut,
      error: controller.signal.aborted ? null : {
        type: String(error?.name || "Error").slice(0, 100),
        message,
        status: Number(error?.status || error?.response?.status || 0),
      },
    });
  }
}
if (timeout) clearTimeout(timeout);
