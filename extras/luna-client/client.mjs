// Standalone manual client. Auth and storage use the official SIWC DevKit.
import {app, safeStorage, shell} from 'electron';
import {createElectronCredentialEncryption} from './credential-encryption.mjs';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {runBatch} from './batch-runner.mjs';
import {clientPaths} from './paths.mjs';

const paths = clientPaths();
const {createChatGPT} = await import(pathToFileURL(join(paths.sdkDir, 'packages/local/dist/index.js')));
app.setName('atproto survey Luna evaluation');
app.setPath('userData', paths.userData);
const mode = process.argv[2];
const output = process.argv[3];
const inputFile = process.argv[4];
if (!['authorize', 'catalog', 'request', 'batch'].includes(mode) || !output) app.exit(2);

await app.whenReady();
await mkdir(paths.credentials, {recursive:true, mode:0o700});
const client = createChatGPT({
  appName:'atproto survey Luna evaluation', appId:'atproto-survey-luna-evaluation',
  redirectPort:0, storageDir:paths.credentials,
  credentialEncryption:createElectronCredentialEncryption(safeStorage,()=>app.isReady()),
  openBrowser: async url => {await shell.openExternal(url); console.log('Authorization browser opened.');},
  sendHostId:true,
});
try {
  let result;
  if (mode === 'authorize') {
    const session = await client.signIn();
    result = {status:session.status,sharing:session.sharing};
  } else if (mode === 'catalog') {
    result = {models:await client.listModels()};
  } else if (mode === 'batch') {
    const spec=JSON.parse(await readFile(inputFile,'utf8'));
    const started=performance.now();
    let activeCalls=0,peakCalls=0;
    const results=await runBatch(spec.items,{parallel:spec.parallel,
      invoke:async item=>{
        const itemStarted=performance.now();
        let metadata,firstDelta,record;
        activeCalls++;peakCalls=Math.max(peakCalls,activeCalls);
        try {
          const response=await client.streamResponse({model:item.model,input:item.prompt,
            reasoningEffort:item.reasoningEffort,
            signal:AbortSignal.timeout(Math.min(180000,item.timeoutMs??180000)),
            onDelta:delta=>{if(delta.length&&firstDelta===undefined)firstDelta=(performance.now()-started)/1000;},
            onCompleted:value=>{metadata=value;}});
          const text=response.text;
          let payload;
          try {
            const stripped=text.trim().replace(/^```(?:json)?\s*/u,'').replace(/\s*```$/u,'');
            payload=JSON.parse(stripped);
          } catch(error) {
            const position=/position (\d+)/u.exec(String(error.message));
            record={id:item.id,status:'invalid_json',code:'invalid_final_json',
              diagnostics:{bytes:Buffer.byteLength(text),sha256:createHash('sha256').update(text).digest('hex'),
                position:position?Number(position[1]):null,first_character:text.trim().slice(0,1),
                last_character:text.trim().slice(-1),code_fence:text.includes('```')}};
          }
          record??={id:item.id,status:'completed',payload,metadata};
        } catch(error) {
          record={id:item.id,status:'failed',code:error.code??'local_error',
            httpStatus:error.status,requestId:error.requestId};
        } finally {activeCalls--;}
        record.call_started_offset_seconds=(itemStarted-started)/1000;
        record.call_completed_offset_seconds=(performance.now()-started)/1000;
        record.first_delta_offset_seconds=firstDelta??null;
        record.inferenceSeconds=(performance.now()-itemStarted)/1000;
        return record;
      },
      persist:async record=>{
        await writeFile(output+'.'+record.id+'.json',JSON.stringify(record,null,2)+'\n',{mode:0o600});
        console.log(JSON.stringify({mode,id:record.id,status:record.status}));
      }});
    result={status:results.every(item=>item.status==='completed')?'completed':'failed',
      parallel:spec.parallel,batchSeconds:(performance.now()-started)/1000,
      peakActiveSDKCalls:peakCalls,
      results:results.map(({payload,metadata,...timing})=>({...timing,result_file:output+'.'+timing.id+'.json'}))};
  } else {
    const request = JSON.parse(await readFile(inputFile,'utf8'));
    const started = performance.now();
    let completedMetadata;
    const response = await client.streamResponse({
      model:request.model, input:request.prompt, reasoningEffort:request.reasoningEffort,
      signal:AbortSignal.timeout(Math.min(180000,request.timeoutMs??180000)),
      onCompleted: metadata => {completedMetadata=metadata;},
    });
    result = {text:response.text, metadata:completedMetadata,
      inferenceSeconds:(performance.now()-started)/1000, status:'completed'};
  }
  await writeFile(output, JSON.stringify(result,null,2)+'\n',{mode:0o600});
  console.log(JSON.stringify({mode,status:result.status??'completed'}));
  app.exit(0);
} catch(error) {
  // Never emit arbitrary server responses, submitted evidence, identity or credentials.
  const diagnostics = {status:'failed', code:error.code??'local_error',
    httpStatus:error.status, requestId:error.requestId};
  await writeFile(output,JSON.stringify(diagnostics,null,2)+'\n',{mode:0o600});
  console.error(JSON.stringify(diagnostics));
  app.exit(1);
}
