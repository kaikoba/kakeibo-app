const fs = require('node:fs/promises');

module.exports = async function globalTeardown() {
  const dbPath = process.env.TSUMUGI_E2E_DB_PATH;
  if (!dbPath) return;

  await Promise.all(
    ['', '-shm', '-wal'].map((suffix) => fs.rm(`${dbPath}${suffix}`, { force: true })),
  );
};
