const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../labhq/web/state.js'), 'utf8'), context);
const create = context.LabHQState.createOfficeState;

// Same shared feed consumed by both 2.5D and 3D, live and snapshot replay.
for (const replay of [false, true]) {
  for (const target of ['cso', 'facilities', 'colleague:peer', 'pi']) {
    for (const rejected of [false, true]) {
      const office = create({now: () => 1000});
      const question = 'Which cohort? ' + 'context '.repeat(100);
      const events = [
        {type: 'agent.ask', agent_id: 'worker', request_id: 'r', ts: 10,
          data: {id: 'a', agent_id: 'worker', to: target, question}},
        {type: 'agent.answer', agent_id: 'cso', request_id: 'r', ts: 11,
          data: {ask_id: 'a', from: 'cso', status: rejected ? 'rejected' : 'answered',
            ...(rejected ? {reason: 'PI denied access'} : {answer: 'Use cases ' + 'detail '.repeat(100)})}},
      ];
      if (replay) office.apply({type: 'snapshot', data: {recent_events: events}});
      else events.forEach(e => office.apply(e));
      assert.equal(office.S.feed.length, 2, '4148905753: both ask events must enter the feed');
      const [answer, ask] = office.S.feed;
      assert.equal(ask.who, 'worker');
      assert.equal(ask.to, target.replace(/^colleague:/, ''));
      assert.match(ask.text, /Which cohort/);
      assert.equal(ask.rid, 'r');
      assert.equal(answer.who, 'cso');
      assert.equal(answer.to, 'worker');
      assert.match(answer.text, /Which cohort/);
      assert.match(answer.text, rejected ? /거절.*PI denied access/ : /Use cases/);
      assert.equal(answer.cls === 'alert', rejected);
      assert.equal(answer.rid, 'r');
      assert.equal(answer.ts, 11);
      assert.ok(ask.text.length < 250 && answer.text.length < 250, 'summarize long prose');
      office.apply({type: 'snapshot', data: {}});
      assert.equal(office.S.feed.length, 0);
    }
  }
}
// A replay buffer may retain the answer after its ask event has expired.
const restored = create();
restored.apply({type: 'agent.answer', agent_id: 'peer', request_id: 'r', data: {
  ask_id: 'expired', from: 'peer', to: 'worker', question: 'Which cohort?',
  status: 'answered', answer: 'Use cases',
}});
assert.equal(restored.S.feed[0].to, 'worker');
assert.match(restored.S.feed[0].text, /Which cohort/);
console.log('web_ask: ask/answer feed, refusal, targets and replay passed');
