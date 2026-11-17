// Synthetic manager/worker sessions for the local PoC; never opens ChatGPT.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import WebSocket from 'ws';

const [port, token, stateRoot, sessionPath, pythonExe] = process.argv.slice(2);
if (!port || !token || !stateRoot || !sessionPath || !pythonExe) {
  throw new Error('usage: node poc-synthetic-extension.mjs port token root session python');
}
const tabs = [
  {id: 101, windowId: 1, index: 0, title: 'PoC Manager (simulated)',
    url: 'https://chatgpt.com/c/11111111-1111-4111-8111-111111111111', active: true},
  {id: 102, windowId: 1, index: 1, title: 'PoC Worker (simulated)',
    url: 'https://chatgpt.com/c/22222222-2222-4222-8222-222222222222', active: false},
];
const session = fs.existsSync(sessionPath) ?
  JSON.parse(fs.readFileSync(sessionPath, 'utf8')) :
  {messages: {'101': [], '102': []}, receipts: {}};
function save() {
  fs.mkdirSync(path.dirname(sessionPath), {recursive:true});
  fs.writeFileSync(sessionPath, JSON.stringify(session, null, 2), 'utf8');
}
const workerCode = [
  'import json,sys',
  'from pathlib import Path',
  'from web_agent_swarm.state import DurableStore',
  'store=DurableStore(Path(sys.argv[1]))',
  'task_id=sys.argv[2]',
  'action=sys.argv[3]',
  'worker="worker-1"',
  'task=store.task_claim(task_id,worker,lease_seconds=90)',
  'if action=="resume":',
  '  store.task_heartbeat(task_id,worker,lease_seconds=90)',
  '  payload=task.get("payload") or {}',
  '  transformed=str(payload.get("text","")).upper()',
  '  done=store.task_release(task_id,worker,status="done",summary="Synthetic worker completed",result={"output":transformed})',
  '  print(json.dumps({"status":done["status"],"output":transformed}))',
  'else:',
  '  print(json.dumps({"status":task["status"],"attempts":task["attempts"]}))'
].join('\n');

const ws = new WebSocket('ws://127.0.0.1:'+port+'/ws?token='+token,{
  origin:'chrome-extension://was-local-poc'
});
ws.on('open',()=>ws.send(JSON.stringify({
  type:'hello', protocolMin:5, protocolMax:5, extensionVersion:'poc-synthetic-1'
})));
ws.on('message',raw=>{
  const msg=JSON.parse(raw.toString());
  if (msg.type==='hello-ack') {
    if(!msg.ok)throw new Error('Protocol negotiation failed');
    return;
  }
  if (msg.type==='list-tabs') {
    ws.send(JSON.stringify({type:'tabs-result',id:msg.id,tabs}));return;
  }
  if (msg.type==='inspect') {
    const tab=tabs.find(t=>t.id===msg.tabId);
    ws.send(JSON.stringify({
      type:'inspect-result',id:msg.id,ok:!!tab,
      tab,error:tab?null:'Unknown PoC tab',
      snapshot:tab?{
        generating:false,uiState:'idle',messages:session.messages[String(tab.id)] || [],
        lastAssistantText:'Synthetic local session; no ChatGPT access'
      }:null
    }));return;
  }
  if (msg.type==='send') {
    const tab=tabs.find(t=>t.id===msg.tabId);
    if (!tab || (msg.expectedUrl && msg.expectedUrl!==tab.url)) {
      ws.send(JSON.stringify({
        type:'send-result',id:msg.id,ok:false,terminal:true,
        mayHaveExecuted:false,error:'Synthetic target not found or URL mismatch'
      }));return;
    }
    const fingerprint=JSON.stringify([msg.tabId,msg.text,msg.expectedUrl]);
    const prior=session.receipts[msg.commandId];
    if(prior) {
      const ok=prior.fingerprint===fingerprint;
      ws.send(JSON.stringify({
        type:'send-result',id:msg.id,ok,duplicate:true,commandId:msg.commandId,
        tabId:tab.id,terminal:!ok,error:ok?null:'Command ID payload mismatch'
      }));return;
    }
    let payload;
    try {payload=JSON.parse(msg.text);}catch {
      ws.send(JSON.stringify({type:'send-result',id:msg.id,ok:false,
        terminal:true,mayHaveExecuted:false,error:'Expected structured PoC instruction'}));return;
    }
    if(tab.id===102) {
      if(!['claim_then_crash','resume'].includes(payload.action))throw new Error('Unknown worker action');
      const result=spawnSync(pythonExe,['-c',workerCode,stateRoot,payload.task_id,payload.action],{
        encoding:'utf8',windowsHide:true,timeout:8000,env:{...process.env}
      });
      if(result.status!==0) {
        ws.send(JSON.stringify({type:'send-result',id:msg.id,ok:false,terminal:true,
          mayHaveExecuted:false,error:'Synthetic worker error: '+result.stderr.slice(0,350)}));
        return;
      }
      if(payload.action==='claim_then_crash') {
        // The durable task lease is active, but the transport ACK is lost.
        process.exit(73);
      }
      session.messages[String(tab.id)].push({
        role:'assistant',text:'Synthetic worker completed: '+result.stdout.trim()
      });
    }else if(payload.type!=='trigger'&&payload.type!=='completion'){
      throw new Error('Unknown synthetic manager message');
    }
    session.messages[String(tab.id)].push({role:'user',text:msg.text});
    session.receipts[msg.commandId]={fingerprint};
    save();
    ws.send(JSON.stringify({
      type:'send-result',id:msg.id,ok:true,commandId:msg.commandId,tabId:tab.id,
      tabUrl:tab.url
    }));
    return;
  }
  if(msg.type==='command-status'){
    const prior=session.receipts[msg.commandId];
    ws.send(JSON.stringify({
      type:'command-status-result',id:msg.id,ok:true,found:!!prior,
      completed:!!prior,commandId:msg.commandId,
      result:prior?{ok:true,commandId:msg.commandId}:null
    }));
  }
});
ws.on('error',error=>{console.error('PoC session connection failed:',error.message);process.exitCode=1;});
