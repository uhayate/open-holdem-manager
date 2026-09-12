// Removes previous build output so every build starts clean and lands in the
// SAME directory (release/) instead of accumulating release-build2, -3, ...
import { rmSync } from 'node:fs'

const targets = ['release', 'backend/dist', 'backend/build', 'frontend/dist']

for (const t of targets) {
  try {
    rmSync(t, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 })
    console.log(`[clean] removed ${t}`)
  } catch (err) {
    console.warn(`[clean] could NOT remove ${t}: ${err.code || err.message}`)
    console.warn('        something is holding a file there (antivirus?). Release it and retry.')
  }
}
