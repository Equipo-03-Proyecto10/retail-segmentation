// engagement-service's MongoDB: the database, its user, three collections, the
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

// The eight kinds of the taxonomy (#798, contract /v2). Each names the
// optional fields it requires; every optional field it does not name is
// forbidden on it, so one kind can never be read as another.
const optionalFields = ["product_id", "label_code", "rank", "campaign_id", "quantity", "screen"];
const kinds = {
  QR_SCAN: ["product_id"],
  RECO_CLICK: ["product_id", "label_code", "rank"],
  RECO_IMPRESSION: ["product_id", "label_code", "rank"],
  STORE_VISIT: [],
  CAMPAIGN_EXPOSURE: ["campaign_id", "customer_id"],
  CART_ADD: ["product_id", "quantity"],
  CART_REMOVE: ["product_id", "quantity"],
  NAVIGATION: ["screen"],
};

function shape(kind) {
  const needs = kinds[kind];
  const forbidden = optionalFields.filter((field) => !needs.includes(field));
  const rule = { properties: { type: { enum: [kind] } } };
  if (needs.length > 0) rule.required = needs;
  if (forbidden.length > 0) {
    rule.not = { anyOf: forbidden.map((field) => ({ required: [field] })) };
  }
  return rule;
}

const eventSchema = {
  bsonType: "object",
  additionalProperties: false,
  required: [
    "_id",
    "client_event_id",
    "type",
    "store_id",
    "recorded_by",
    "occurred_at",
    "recorded_at",
    "voided",
    "history",
  ],
  properties: {
    _id: { bsonType: "string", pattern: uuid },
    client_event_id: { bsonType: "string", pattern: uuid },
    type: { enum: Object.keys(kinds) },
    store_id: { bsonType: "int" },
    product_id: { bsonType: "int" },
    customer_id: { bsonType: "string", pattern: uuid },
    label_code: { bsonType: "string", minLength: 1, maxLength: 40 },
    rank: { bsonType: "int", minimum: 1 },
    campaign_id: { bsonType: "int", minimum: 1 },
    quantity: { bsonType: "int", minimum: 1, maximum: 1000 },
    screen: { bsonType: "string", minLength: 1, maxLength: 40 },
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
    // The outbox of ADR-0075: the notices still to relay on
    // stream:engagement.v1.interaction_recorded, in order.
    relay_pending: {
      bsonType: "array",
      maxItems: 2,
      items: { enum: ["RECORDED", "VOIDED"] },
    },
  },
  oneOf: Object.keys(kinds).map(shape),
};

// A refused queued write, kept as a deliberately small allow-list. In
// particular, there is nowhere to store an authorization header, cookie,
// token, password or customer details (ADR-0071).
const incidentSchema = {
  bsonType: "object",
  additionalProperties: false,
  required: [
    "_id",
    "client_incident_id",
    "source_type",
    "source_operation_id",
    "source_correlation_id",
    "store_id",
    "refused_status",
    "refused_code",
    "attempts",
    "occurred_at",
    "line_count",
    "reported_by",
    "recorded_at",
    "status",
    "history",
  ],
  properties: {
    _id: { bsonType: "string", pattern: uuid },
    client_incident_id: { bsonType: "string", pattern: uuid },
    source_type: {
      enum: ["SALE", "CHECK_IN", "ENGAGEMENT_EVENT", "CUSTOMER_WRITE"],
    },
    source_operation_id: { bsonType: "string", pattern: uuid },
    source_correlation_id: {
      bsonType: "string",
      pattern: "^[0-9A-Za-z-]{1,64}$",
    },
    store_id: { bsonType: "int" },
    refused_status: { bsonType: "int", minimum: 400, maximum: 499 },
    refused_code: { bsonType: "string", pattern: "^[a-z][a-z0-9_]{0,63}$" },
    attempts: { bsonType: "int", minimum: 1 },
    occurred_at: { bsonType: "date" },
    line_count: { bsonType: ["int", "null"], minimum: 1 },
    reported_by: { bsonType: "string", pattern: uuid },
    recorded_at: { bsonType: "date" },
    status: { enum: ["OPEN", "ACKNOWLEDGED", "RESOLVED"] },
    history: {
      bsonType: "array",
      items: {
        bsonType: "object",
        additionalProperties: false,
        required: ["status", "note", "by", "at"],
        properties: {
          status: { enum: ["ACKNOWLEDGED", "RESOLVED"] },
          note: { bsonType: ["string", "null"], minLength: 1, maxLength: 200 },
          by: { bsonType: "string", pattern: uuid },
          at: { bsonType: "date" },
        },
      },
    },
  },
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
if (!names.includes("capture_incident")) {
  engagementDb.createCollection("capture_incident", {
    validator: { $jsonSchema: incidentSchema },
    validationLevel: "strict",
    validationAction: "error",
  });
  print("init.js: created capture_incident");
} else {
  engagementDb.runCommand({
    collMod: "capture_incident",
    validator: { $jsonSchema: incidentSchema },
    validationLevel: "strict",
    validationAction: "error",
  });
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
      Boolean(have[name].unique) !== Boolean(want.unique) ||
      JSON.stringify(have[name].partialFilterExpression || null) !==
        JSON.stringify(want.partial || null)
    ) {
      collection.dropIndex(name);
      print("init.js: dropped index " + collection.getName() + "." + name);
      delete have[name];
    }
  });
  wanted.forEach((want) => {
    if (!have[want.name]) {
      const options = { name: want.name, unique: Boolean(want.unique) };
      if (want.partial) options.partialFilterExpression = want.partial;
      collection.createIndex(want.key, options);
      print("init.js: created index " + collection.getName() + "." + want.name);
    }
  });
}

converge(engagementDb.engagement_event, [
  { name: "client_event_id_1", key: { client_event_id: 1 }, unique: true },
  { name: "store_id_1_occurred_at_-1", key: { store_id: 1, occurred_at: -1 } },
  { name: "type_1_occurred_at_-1", key: { type: 1, occurred_at: -1 } },
  { name: "customer_id_1_occurred_at_-1", key: { customer_id: 1, occurred_at: -1 } },
  // The relay's scan (ADR-0075): only events with a notice still to publish,
  // in occurred_at order. `$pull` leaves [], so the filter tests element 0.
  {
    name: "relay_pending_occurred_at_1",
    key: { occurred_at: 1 },
    partial: { "relay_pending.0": { $exists: true } },
  },
]);
converge(engagementDb.engagement_daily, [
  { name: "store_id_1_day_1_type_1", key: { store_id: 1, day: 1, type: 1 }, unique: true },
]);
converge(engagementDb.capture_incident, [
  { name: "client_incident_id_1", key: { client_incident_id: 1 }, unique: true },
  {
    name: "source_type_1_source_operation_id_1",
    key: { source_type: 1, source_operation_id: 1 },
    unique: true,
  },
  {
    name: "store_id_1_status_1_recorded_at_-1",
    key: { store_id: 1, status: 1, recorded_at: -1 },
  },
]);

print("init.js: engagement is ready");
