// engagement-service's MongoDB: the database, its user, both collections, the
// validator and the indexes (ADR-0038).
//
// Run by an administrator, as root, in mongosh, before the service version
// that needs it, and again whenever this file changes:
//
//   MONGO_ENGAGEMENT_PASSWORD=... mongosh "$ROOT_URI" --file services/engagement/mongo/init.js
//
// It is idempotent: a second run changes nothing, and a run against an older
// shape brings the validator and the indexes to the definition below. The
// service itself never creates, alters or drops any of it; it only checks.
// The script is also inlined after root's sign-in by the test stack, so it
// reads like a statement list and needs no other file.

const engagementPassword = process.env.MONGO_ENGAGEMENT_PASSWORD;
if (!engagementPassword) {
  print("init.js: MONGO_ENGAGEMENT_PASSWORD is not set; nothing was changed");
  process.exit(1);
}

const engagementDb = db.getSiblingDB("engagement");

// ---- the user: readWrite on `engagement` and nothing else -----------------

const wantedRoles = [{ role: "readWrite", db: "engagement" }];
const existingUser = engagementDb.getUser("svc_engagement");
if (existingUser === null) {
  engagementDb.createUser({
    user: "svc_engagement",
    pwd: engagementPassword,
    roles: wantedRoles,
  });
  print("init.js: created user svc_engagement");
} else {
  // Does the stored password already match? Asked on a second connection, so
  // root's own sign-in is left alone.
  let passwordHolds = true;
  let probe = null;
  try {
    probe = new Mongo(db.getMongo().getURI());
    probe.getDB("engagement").auth("svc_engagement", engagementPassword);
  } catch (e) {
    passwordHolds = false;
  } finally {
    if (probe !== null) probe.close();
  }
  const sameRoles =
    JSON.stringify(existingUser.roles) === JSON.stringify(wantedRoles);
  if (!passwordHolds || !sameRoles) {
    engagementDb.updateUser("svc_engagement", {
      pwd: engagementPassword,
      roles: wantedRoles,
    });
    print("init.js: updated user svc_engagement");
  }
}

// ---- engagement_event: a strict validator -------------------------------

const uuid = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$";

const eventSchema = {
  bsonType: "object",
  additionalProperties: false,
  required: [
    "_id",
    "client_event_id",
    "type",
    "store_id",
    "product_id",
    "recorded_by",
    "occurred_at",
    "recorded_at",
    "voided",
    "history",
  ],
  properties: {
    _id: { bsonType: "string", pattern: uuid },
    client_event_id: { bsonType: "string", pattern: uuid },
    type: { enum: ["QR_SCAN", "RECO_CLICK"] },
    store_id: { bsonType: "int" },
    product_id: { bsonType: "int" },
    customer_id: { bsonType: "string", pattern: uuid },
    label_code: { bsonType: "string", minLength: 1, maxLength: 40 },
    rank: { bsonType: "int", minimum: 1 },
    recorded_by: { bsonType: "string", minLength: 1 },
    occurred_at: { bsonType: "date" },
    recorded_at: { bsonType: "date" },
    voided: { bsonType: "bool" },
    history: {
      bsonType: "array",
      items: {
        bsonType: "object",
        additionalProperties: false,
        required: ["action", "reason", "by", "at"],
        properties: {
          action: { enum: ["VOIDED"] },
          reason: { bsonType: "string", minLength: 1, maxLength: 500 },
          by: { bsonType: "string", minLength: 1 },
          at: { bsonType: "date" },
        },
      },
    },
  },
  // Two shapes: a scan never carries a label or a rank, a click always does.
  oneOf: [
    {
      properties: { type: { enum: ["QR_SCAN"] } },
      not: { anyOf: [{ required: ["label_code"] }, { required: ["rank"] }] },
    },
    {
      properties: { type: { enum: ["RECO_CLICK"] } },
      required: ["label_code", "rank"],
    },
  ],
};

const names = engagementDb.getCollectionNames();
if (!names.includes("engagement_event")) {
  engagementDb.createCollection("engagement_event", {
    validator: { $jsonSchema: eventSchema },
    validationLevel: "strict",
    validationAction: "error",
  });
  print("init.js: created engagement_event");
} else {
  // Brings an older validator to this definition; the same one is a no-op.
  engagementDb.runCommand({
    collMod: "engagement_event",
    validator: { $jsonSchema: eventSchema },
    validationLevel: "strict",
    validationAction: "error",
  });
}
if (!names.includes("engagement_daily")) {
  engagementDb.createCollection("engagement_daily");
  print("init.js: created engagement_daily");
}

// ---- indexes: exactly these, no more --------------------------------------

function converge(collection, wanted) {
  const have = {};
  collection.getIndexes().forEach((index) => {
    have[index.name] = index;
  });
  Object.keys(have).forEach((name) => {
    if (name === "_id_") return;
    const want = wanted.find((w) => w.name === name);
    if (
      !want ||
      JSON.stringify(have[name].key) !== JSON.stringify(want.key) ||
      Boolean(have[name].unique) !== Boolean(want.unique)
    ) {
      collection.dropIndex(name);
      print("init.js: dropped index " + collection.getName() + "." + name);
      delete have[name];
    }
  });
  wanted.forEach((want) => {
    if (!have[want.name]) {
      collection.createIndex(want.key, { name: want.name, unique: Boolean(want.unique) });
      print("init.js: created index " + collection.getName() + "." + want.name);
    }
  });
}

converge(engagementDb.engagement_event, [
  { name: "client_event_id_1", key: { client_event_id: 1 }, unique: true },
  { name: "store_id_1_occurred_at_-1", key: { store_id: 1, occurred_at: -1 } },
  { name: "type_1_occurred_at_-1", key: { type: 1, occurred_at: -1 } },
  { name: "customer_id_1_occurred_at_-1", key: { customer_id: 1, occurred_at: -1 } },
]);
converge(engagementDb.engagement_daily, [
  { name: "store_id_1_day_1_type_1", key: { store_id: 1, day: 1, type: 1 }, unique: true },
]);

print("init.js: engagement is ready");
