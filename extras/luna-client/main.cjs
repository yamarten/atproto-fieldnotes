const {app} = require('electron');
const path = require('node:path');
const os = require('node:os');
app.setName('atproto survey Luna evaluation');
const stateDir = path.resolve(process.env.LUNA_STATE_DIR || path.join(os.homedir(), 'Library/Application Support/atproto-survey/luna-client'));
app.setPath('userData', path.join(stateDir,'electron-user-data'));
console.log('Manual Luna client started.');
app.whenReady().then(()=>import('./client.mjs')).catch(()=>{
  console.error('Manual Luna client initialization failed.');
  app.exit(1);
});
