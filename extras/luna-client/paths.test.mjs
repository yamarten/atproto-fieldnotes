import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {clientPaths} from './paths.mjs';
import {createElectronCredentialEncryption} from './credential-encryption.mjs';

test('account state stays outside the checkout and SDK can be selected separately', () => {
  assert.equal(clientPaths({}, '/example/home').credentials,
    '/example/home/Library/Application Support/atproto-survey/luna-client/credentials');
  assert.deepEqual(clientPaths({LUNA_STATE_DIR:'/private/state', LUNA_SDK_DIR:'/private/sdk'}),
    {stateDir:'/private/state', sdkDir:'/private/sdk', credentials:'/private/state/credentials', userData:'/private/state/electron-user-data'});
});

test('launcher uses selected runtime and source directory without creating account state', t => {
  const root = mkdtempSync(join(tmpdir(), 'luna-launcher-'));
  t.after(() => rmSync(root, {recursive:true, force:true}));
  const sdk = join(root, 'sdk with spaces');
  const binaryDir = join(sdk, 'node_modules/electron/dist/Electron.app/Contents/MacOS');
  mkdirSync(binaryDir, {recursive:true});
  writeFileSync(join(binaryDir, 'Electron'), '#!'+process.execPath+'\nprocess.stdout.write(JSON.stringify(process.argv.slice(2)));\n', {mode:0o700});
  const state = join(root, 'unused state');
  const launcher = fileURLToPath(new URL('./luna', import.meta.url));
  const env = {...process.env, LUNA_SDK_DIR:sdk, LUNA_STATE_DIR:state};
  const result = spawnSync(launcher, ['catalog', 'output with spaces.json'], {env, encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [fileURLToPath(new URL('.', import.meta.url)).replace(/\/$/u, ''), 'catalog', 'output with spaces.json']);
  assert.equal(existsSync(state), false);
  assert.equal(spawnSync(launcher, ['--help'], {env:{...env, LUNA_SDK_DIR:root}}).status, 0);
  assert.equal(spawnSync(launcher, ['catalog', 'out'], {env:{...env, LUNA_SDK_DIR:root}}).status, 2);
});

test('credential adapter refuses unavailable encryption and never falls back to plaintext', () => {
  let encrypted = false;
  const storage = {isEncryptionAvailable:()=>false, encryptString:()=>{encrypted=true;}};
  const adapter = createElectronCredentialEncryption(storage, ()=>true, 'darwin');
  assert.equal(adapter.isAvailable(), false);
  assert.throws(()=>adapter.encrypt('synthetic credential'), /unavailable/u);
  assert.equal(encrypted, false);
});

test('credential adapter refuses calls before Electron is ready', () => {
  const adapter = createElectronCredentialEncryption({isEncryptionAvailable:()=>true}, ()=>false, 'darwin');
  assert.throws(()=>adapter.decrypt(Buffer.from('synthetic ciphertext')), /unavailable/u);
});
