// Isolated content-script test using a synthetic DOM, not a ChatGPT session.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const here=path.dirname(fileURLToPath(import.meta.url));
const code=readFileSync(path.join(here,'../browser-extension/content.js'),'utf8');
const expected='https://chatgpt.com/c/recipient-A';
const other='https://chatgpt.com/c/recipient-B';

async function scenario(initialUrl,changeDuringComposition=false) {
  const location={href:initialUrl};
  let clicks=0,changes=0,received=null;
  const sendButton={disabled:false,click(){clicks++}};
  const input={
    focus(){},
    replaceChildren(){},
    appendChild(){},
    dispatchEvent(event) {
      if(event.type==='input'){
        changes++;
        if(changeDuringComposition)location.href=other;
      }
    }
  };
  const document={
    querySelector(query){
      if(query==='#prompt-textarea')return input;
      if(query==='[data-testid="send-button"]')return sendButton;
      return null;
    },
    createElement(){return {textContent:''};}
  };
  class FakeEvent {constructor(type){this.type=type;}}
  const context={
    chrome:{runtime:{onMessage:{addListener(fn){received=fn;}}}},
    location,document,
    HTMLTextAreaElement:class {},HTMLInputElement:class {},
    InputEvent:FakeEvent,Event:FakeEvent,
    setTimeout
  };
  vm.runInNewContext(code,context,{filename:'content.js'});
  assert.equal(typeof received,'function');
  const result=await new Promise(resolve=>received({
    type:'send-chatgpt-message',text:'Synthetic instruction',expectedUrl:expected
  },{},resolve));
  return {result,clicks,changes};
}

const wrongAtStart=await scenario(other);
assert.equal(wrongAtStart.result.ok,false);
assert.equal(wrongAtStart.result.mayHaveExecuted,false);
assert.equal(wrongAtStart.result.terminal,true);
assert.equal(wrongAtStart.clicks,0);
assert.equal(wrongAtStart.changes,0);

const changedWhileComposing=await scenario(expected,true);
assert.equal(changedWhileComposing.result.ok,false);
assert.equal(changedWhileComposing.result.mayHaveExecuted,false);
assert.equal(changedWhileComposing.result.terminal,true);
assert.equal(changedWhileComposing.clicks,0);

const correct=await scenario(expected);
assert.equal(correct.result.ok,true);
assert.equal(correct.clicks,1);
console.log('PASS: content script refuses wrong URL, refuses mid-send navigation, submits only to expected conversation');
