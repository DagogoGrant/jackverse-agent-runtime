import fs from 'fs';

export default async function globalTeardown() {
  const dbPaths = [
    '/tmp/caseworker_e2e_integration.db',
    '/tmp/caseworker_e2e_integration.db-wal',
    '/tmp/caseworker_e2e_integration.db-shm',
  ];

  for (const p of dbPaths) {
    if (fs.existsSync(p)) {
      try {
        fs.unlinkSync(p);
      } catch {
        // Ignore errors during unlink
      }
    }
  }
}
