const appDb = process.env.MONGO_INITDB_DATABASE;
const appUser = process.env.MONGO_APP_USER;
const appPwd = process.env.MONGO_APP_PWD;

print(`[init] creating app user '${appUser}' on db '${appDb}'`);

db = db.getSiblingDB(appDb);
db.createUser({
  user: appUser,
  pwd: appPwd,
  roles: [{ role: "readWrite", db: appDb }],
});

print(`[init] creating index on 'player' collection for unique 'playerTag'`);
db.players.createIndex({ playerTag: 1 }, { unique: true, name: "tag_unique" });

// The API's player search reads only the players changed since its last
// read. NOTE The API also creates this index at startup, for databases that
// existed before it; keep the name in sync with ensure_search_change_index.
print(`[init] creating index on 'player' collection for 'searchChangedAt'`);
db.players.createIndex({ searchChangedAt: 1 }, { name: "searchChangedAt_index" });

print(`[init] creating index on 'game_modes' collection for unique 'name'`);
db.game_modes.createIndex({ name: 1 }, { unique: true, name: "name_unique" });

print(
  `[init] creating index on 'battles' collection for 'battleTime' and 'referencePlayerTag'`,
);

db.battles.createIndex(
  { referencePlayerTag: 1, battleTime: -1 },
  { unique: true, name: "referencePlayerTag_battleTime_index" },
);

print(
  `[init] creating index on 'battles' collection for 'gameMode' filtered stats`,
);

// Mode-filtered stats seek one time range per selected mode instead of
// fetching and discarding every other mode's battles.
db.battles.createIndex(
  { referencePlayerTag: 1, gameMode: 1, battleTime: -1 },
  { name: "referencePlayerTag_gameMode_time_index" },
);
