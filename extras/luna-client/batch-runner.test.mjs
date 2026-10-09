import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runBatch} from './batch-runner.mjs';

const items = [{id:'A'}, {id:'B'}];
test('parallel sibling completes and persists independently after other failure', async () => {
  let release;
  const gate = new Promise(resolve => {release=resolve;});
  const saved=[];
  const results=await runBatch(items,{parallel:2,
    invoke: async item => {
      if(item.id==='A') {await gate; return {id:'A',status:'completed',payload:{tagline:'保存確認'}};}
      release(); return {id:'B',status:'failed',code:'simulated_error'};
    },persist:async result=>saved.push(result)});
  assert.equal(saved.length,2);
  assert.equal(results[0].payload.tagline,'保存確認');
  assert.equal(results[1].status,'failed');
});
test('serial failure does not start the next request',async()=>{
  const started=[]; const saved=[];
  const results=await runBatch(items,{parallel:1,
    invoke:async item=>{started.push(item.id);return {id:item.id,status:'failed'};},
    persist:async result=>saved.push(result)});
  assert.deepEqual(started,['A']);assert.equal(saved.length,1);
  assert.equal(results[1].status,'not_started');
});
test('serial success finishes and persists first before starting second',async()=>{
  const events=[];
  await runBatch(items,{parallel:1,
    invoke:async item=>{events.push('start:'+item.id);return {id:item.id,status:'completed'};},
    persist:async result=>events.push('save:'+result.id)});
  assert.deepEqual(events,['start:A','save:A','start:B','save:B']);
});
test('parallel waits for surviving persistence even if other persistence throws',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});let survivor=false;
  await assert.rejects(runBatch(items,{parallel:2,
    invoke:async item=>({id:item.id,status:'completed'}),persist:async result=>{
      if(result.id==='A'){release();throw new Error('simulated disk error');}
      await gate;survivor=true;
    }}),/persistence failed/);
  assert.equal(survivor,true);
});
