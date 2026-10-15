const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const web=process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname,'../labhq/web');
const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(web,'state.js'),'utf8'),context);
const state=context.LabHQState;
const load=()=>import('data:text/javascript;base64,'+fs.readFileSync(path.join(web,'ui/office-art.js')).toString('base64'));
const css=()=>fs.readFileSync(path.join(web,'ui/office.css'),'utf8');

test('every roster animal has a full body and its own role prop, including facilities and contractors',async()=>{
  const ui=await load();
  const expected={chief_of_staff:['penguin','clipboard'],cso:['owl','clipboard'],biologist:['bear','leaf'],
    data_steward:['squirrel','folder'],lit_scout:['fox','book'],analyst:['raccoon','laptop'],
    engineer:['octopus','wrench'],qc_reviewer:['hedgehog','magnifier'],sci_reviewer:['turtle','checklist'],
    recruiter:['capybara','businesscard'],editor:['beaver','pen'],solo:['lion','checklist'],'bioinfo-agent':['otter','laptop'],facilities:['hamster','tools'],c_demo:['chick','badge']};
  for(const [id,[kind,prop]] of Object.entries(expected)){
    const a={id},svg=ui.officeSVG(a,state.staffCharacter(a));
    assert.match(svg,new RegExp(`data-character="${kind}"`),id);
    assert.match(svg,new RegExp(`data-prop="${prop}"`),id);
    assert.match(svg,/class="office-body"/);assert.match(svg,/class="office-prop"/);
  }
  assert.equal(ui.officeProp({id:'analyst',employment:'contract'}),'badge');
  assert.match(ui.officeSVG({id:'CSO'},state.staffCharacter({id:'CSO'})),/data-character="owl"/);
});
test('every core roster staff member has its own office prop and sits in an office zone (editor carried over from #590)',async()=>{
  const ui=await load();
  const dir=path.resolve(__dirname,'../agents/core');
  const core=fs.readdirSync(dir).filter(f=>f.endsWith('.yaml')).map(f=>{
    const text=fs.readFileSync(path.join(dir,f),'utf8');
    return {id:text.match(/^id:\s*(\S+)/m)[1],character:text.match(/^character:\s*(\S+)/m)[1]};
  });
  assert.ok(core.length>=13,'core roster is read from agents/core');
  const zones=Function('return '+html.match(/const ZONES = (\[[\s\S]*?\n\]);/)[1])();
  const seated=zones.flatMap(([,,ids])=>ids);
  assert.equal(new Set(seated).size,seated.length,'no staff member sits in two zones');
  for(const {id,character} of core){
    assert.ok(seated.includes(id),id+' has a seat in ZONES');
    assert.notEqual(ui.officeProp({id}),'badge',id+' has a role prop, not the contractor badge');
    assert.match(ui.officeSVG({id},state.staffCharacter({id,character})),new RegExp('data-character="'+(id==='facilities'?'hamster':character)+'"'),id);
  }
  assert.equal(zones.find(([,,ids])=>ids.includes('editor'))[0],'hr','editor sits with operations, not research');
  assert.equal(zones.find(([,,ids])=>ids.includes('recruiter'))[0],'hr');
  assert.equal(ui.officeProp({id:'editor'}),'pen');
  assert.equal(ui.officeProp({id:'editor',employment:'contract'}),'badge','a contract badge still wins');
  const svg=ui.officeSVG({id:'editor'},'beaver');
  assert.match(svg,/data-prop="pen"/);assert.doesNotMatch(svg,/<image|<use|href=|url\(|https?:/i);
});
test('unknown and unsupported characters retain a neutral question-mark before and after roster',async()=>{
  const ui=await load();
  for(const a of [{id:'new'},{id:'new',character:'not-a-known-animal'},{id:'<img src=x>',character:'__proto__'}]){
    const svg=ui.officeSVG(a,state.staffCharacter(a));
    assert.match(svg,/data-character="neutral"/);assert.match(svg,/>\?<\/text>/);assert.doesNotMatch(svg,/<img/);
  }
  assert.match(ui.officeHead('neutral'),/>\?<\/text>/);
  assert.equal(ui.officeProp({id:'__proto__'}),'badge');
  assert.match(html,/officeCharacter\(a, LabHQState.staffCharacter\(a\)\)/);
});
test('work, answer, approval, rest and completion show dot plus text without inventing approval for stale wait',async()=>{
  const {officeStatus}=await load();
  const expected=[['working','작업 중','working','일하는 중'],['hibernating','답 기다리는 중','answer','답 기다리는 중'],
    ['waiting','PI 결정 기다리는 중','decision','승인 기다려요'],['idle','쉬는 중','idle','쉬는 중'],['done','완료','done','끝남']];
  for(const [vs,detail,kind,text] of expected)assert.deepEqual(officeStatus({},vs,detail),{kind,text});
  const office=state.createOfficeState();
  office.apply({type:'snapshot',data:{agents:[{id:'analyst',state:'waiting'}]}});
  const a=office.ag('analyst');
  assert.notEqual(officeStatus(a,office.visual(a),office.stateText(a)).kind,'decision');
  assert.equal(officeStatus({},'waiting','일시정지').kind,'paused');
  assert.equal(officeStatus({},'done','작업 끝 · PI 검토 대기').kind,'decision');
  assert.match(html,/class="office-status".*class="status-dot".*class="office-status-text"/);
  assert.match(html,/officeStatus\(a, vs, stateText\(a, vs\)\)/);
  assert.match(css(),/\[data-status="decision"\][^{]*\{[^}]*var\(--ds-accent\)/);
});
test('motion is small, gated by OS preference, and forcibly absent in reduced motion',()=>{
  const source=css();
  assert.match(source,/@media \(prefers-reduced-motion: no-preference\)/);
  assert.match(source,/@media \(prefers-reduced-motion: reduce\)[\s\S]*animation: none !important; transition: none !important/);
  for(const status of ['working','answer','decision','idle','done'])assert.ok(source.includes(`.seat[data-status="${status}"]`));
});
test('phone prioritizes core staff in content-sized rooms and never shrinks the proof below 64px',()=>{
  const source=css();
  assert.match(source,/\.zones[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(source,/\.seats[^}]*repeat\(auto-fit, minmax\(130px, 1fr\)\)/);
  assert.match(source,/data-zone="data"[^}]*order: -2/);
  assert.match(source,/data-zone="lead"[^}]*order: -1/);
  assert.match(source,/flex-basis: 64px; width: 64px; height: 64px/);
  assert.doesNotMatch(html,/HOT_DESKS|EMPTY_SEAT/);
  assert.match(html,/a\.role,a\.character,a\.employment/); // late roster redraws art and role
});
test('office assets are inline/local and the production page loads them',async()=>{
  const ui=await load();
  assert.match(html,/href="ui\/office.css"/);assert.match(html,/from '\.\/ui\/office-art.js'/);
  assert.doesNotMatch(html,/<link[^>]+href="https?:\/\//);
  for(const kind of ['owl','raccoon','hedgehog','octopus','fox','hamster','neutral']){
    assert.doesNotMatch(ui.officeSVG({},kind),/<image|<use|href=|url\(|https?:/i);
  }
  assert.doesNotMatch(css(),/url\(|@import/i);
});
test('light and dark text meet AA, dots and illustration outlines meet 3:1',()=>{
  const tokens=fs.readFileSync(path.join(web,'ui/tokens.css'),'utf8');
  const colors=Object.fromEntries([...tokens.matchAll(/--ds-([\w-]+):\s*light-dark\((#[\da-f]+),\s*(#[\da-f]+)\)/gi)].map(m=>[m[1],m.slice(2)]));
  const lum=h=>{const c=h.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722;};
  const ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
  for(let theme=0;theme<2;theme++)for(const [ink,paper]of [['ink','surface'],['ink-2','surface'],['ink-2','sunk'],['work','surface'],['err','surface'],['accent','accent-soft'],['ink','accent-soft'],['ink-2','accent-soft']]){
    assert.ok(ratio(colors[ink][theme],colors[paper][theme])>=4.5,`${theme} ${ink}/${paper}`);
  }
  for(const [ink,paper]of [['#303638','#F2EFE9'],['#394F55','#EFF4EE']])assert.ok(ratio(ink,paper)>=3);
});
