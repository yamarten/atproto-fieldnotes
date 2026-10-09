import {homedir} from 'node:os';
import {join, resolve} from 'node:path';

// Keep account state outside the checkout, including when used from a worktree.
export function clientPaths(env = process.env, home = homedir()) {
  const stateDir = resolve(env.LUNA_STATE_DIR || join(home, 'Library/Application Support/atproto-survey/luna-client'));
  const sdkDir = resolve(env.LUNA_SDK_DIR || join(stateDir, 'devkit'));
  return {stateDir, sdkDir, credentials:join(stateDir, 'credentials'),
    userData:join(stateDir, 'electron-user-data')};
}
