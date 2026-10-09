import assert from 'node:assert/strict';
import test from 'node:test';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {clientPaths} from './paths.mjs';
const {streamResponse} = await import(pathToFileURL(join(clientPaths().sdkDir, 'packages/local/dist/responses.js')));

test('evaluation passes explicit effort, records completion, and adds no instructions or tools', async (t) => {
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body), {
      model: 'gpt-6-luna', input: [{role: 'user', content: 'fixed evidence'}],
      reasoning: {effort: 'high'}, store: false, stream: true,
    });
    return new Response('data: '+JSON.stringify({type:'response.completed', response:{
      status:'completed', model:'gpt-6-luna', reasoning:{effort:'high'}, usage:{output_tokens:12},
    }})+'\n\n', {headers:{'content-type':'text/event-stream','x-request-id':'req_eval'}});
  });
  let metadata;
  assert.deepEqual(await streamResponse('synthetic-token', {
    model:'gpt-6-luna', input:'fixed evidence', reasoningEffort:'high',
    onCompleted: value => {metadata=value;},
  }, new AbortController().signal), {text:''});
  assert.deepEqual(metadata, {
    model:'gpt-6-luna', reasoning:{effort:'high'}, usage:{output_tokens:12}, requestId:'req_eval',
  });
});
