// Demonstration data for engagement-service (ADR-0038): 40 QR scans and 36
// recommendation clicks over the seeded stores (1-6 of 30), products (1-40) and
// customers (00000000-0000-0000-0000-0000000000NN, NN 1-30) of
// sql/02_seed_30_per_table.sql, on ten fixed UTC days from 2026-10-01.
//
// Run after init.js, in mongosh, as root or as svc_engagement:
//
//   mongosh "$MONGO_URI" --file services/engagement/mongo/seed.js
//
// Every client_event_id and event id is fixed, and an event is written only
// when its client_event_id is not stored yet, so a second run inserts nothing.
// It ends by rebuilding engagement_daily for the days it seeds, with the same
// pipeline as the service's maintenance command. The data is synthetic.

const seedDb = db.getSiblingDB("engagement");
const events = seedDb.engagement_event;
const daily = seedDb.engagement_daily;

const hex = (n, width) => n.toString(16).padStart(width, "0");
const customerUuid = (n) => "00000000-0000-0000-0000-" + n.toString().padStart(12, "0");
const labels = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"];
const recordedBy = "5eed0000-0000-4000-8000-000000000001"; // the demonstration recorder
const firstDay = "2026-10-01";
const lastDay = "2026-10-14";

function occurredAt(i) {
  return new Date(Date.UTC(2026, 9, 1 + (i % 10), 8 + (i % 12), (i * 7) % 60, 0));
}

const seeded = [];
for (let i = 0; i < 40; i++) {
  const event = {
    _id: "5eed0001-0000-4000-8000-" + hex(i + 1, 12),
    client_event_id: "5eed0002-0000-4000-8000-" + hex(i + 1, 12),
    type: "QR_SCAN",
    store_id: NumberInt(1 + ((i * 7) % 6)),
    product_id: NumberInt(1 + ((i * 11) % 40)),
    recorded_by: recordedBy,
    occurred_at: occurredAt(i),
    recorded_at: new Date(occurredAt(i).getTime() + 2000),
    voided: false,
    history: [],
  };
  if (i % 3 !== 0) event.customer_id = customerUuid(1 + ((i * 5) % 30));
  seeded.push(event);
}
for (let i = 0; i < 36; i++) {
  const event = {
    _id: "5eed0003-0000-4000-8000-" + hex(i + 1, 12),
    client_event_id: "5eed0004-0000-4000-8000-" + hex(i + 1, 12),
    type: "RECO_CLICK",
    store_id: NumberInt(1 + ((i * 11) % 6)),
    product_id: NumberInt(1 + ((i * 13) % 40)),
    label_code: labels[i % labels.length],
    rank: NumberInt(1 + (i % 5)),
    recorded_by: recordedBy,
    occurred_at: occurredAt(i + 3),
    recorded_at: new Date(occurredAt(i + 3).getTime() + 1000),
    voided: false,
    history: [],
  };
  if (i % 4 !== 0) event.customer_id = customerUuid(1 + ((i * 7) % 30));
  seeded.push(event);
}
// Three events were voided after the fact, so the rollup has something to leave out.
[4, 17, 33].forEach((i) => {
  const event = seeded[i];
  event.voided = true;
  event.history = [
    {
      action: "VOIDED",
      reason: "Seeded: scanned the wrong shelf label.",
      by: recordedBy,
      at: new Date(event.recorded_at.getTime() + 3600 * 1000),
    },
  ];
});

let inserted = 0;
seeded.forEach((event) => {
  const result = events.updateOne(
    { client_event_id: event.client_event_id },
    { $setOnInsert: event },
    { upsert: true }
  );
  inserted += result.upsertedCount;
});

// ---- the rollup, rebuilt for the seeded days ------------------------------

const span = { day: { $gte: firstDay, $lte: lastDay } };
const rebuilt = events
  .aggregate([
    {
      $match: {
        voided: false,
        occurred_at: { $gte: new Date(firstDay + "T00:00:00Z"), $lt: new Date("2026-10-15T00:00:00Z") },
      },
    },
    {
      $group: {
        _id: {
          store_id: "$store_id",
          type: "$type",
          day: { $dateToString: { format: "%Y-%m-%d", date: "$occurred_at", timezone: "UTC" } },
        },
        count: { $sum: 1 },
      },
    },
    {
      $project: { _id: 0, store_id: "$_id.store_id", day: "$_id.day", type: "$_id.type", count: 1 },
    },
  ])
  .toArray();
rebuilt.forEach((row) => {
  daily.replaceOne({ store_id: row.store_id, day: row.day, type: row.type }, row, { upsert: true });
});
// A group whose events are all voided is absent above; its count is zero.
const present = new Set(rebuilt.map((r) => r.store_id + "|" + r.day + "|" + r.type));
daily.find({ ...span, count: { $ne: 0 } }).forEach((row) => {
  if (!present.has(row.store_id + "|" + row.day + "|" + row.type)) {
    daily.updateOne({ _id: row._id }, { $set: { count: 0 } });
  }
});

print(
  "seed.js: " + inserted + " events inserted, " + events.countDocuments({}) +
    " stored, " + rebuilt.length + " daily groups"
);
