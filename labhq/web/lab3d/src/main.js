import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Shapes,C } from './primitives.js';
import { STATES,STATE_LABELS,STATE_COLORS } from './characters.js';
import { buildOffice } from './office.js';
import { rosterFor,selectionsFor,buildSkin,buildProcedural } from './skins.js';
import { buildStatusLayer } from './status-layer.js';

const $=id=>document.getElementById(id),stage=$('stage');
const reducedQuery=matchMedia('(prefers-reduced-motion: reduce)');
let reduced=reducedQuery.matches,view='office',selected='cso',focused=false,silhouette=false;
const scene=new THREE.Scene();scene.background=new THREE.Color(C.paper);
const camera=new THREE.OrthographicCamera(-12,12,9,-9,.1,160);
let renderer;
try {renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});}catch(e){$('error').textContent='WebGL2를 사용할 수 없습니다. WebGL2를 지원하는 브라우저에서 열어 주세요.';$('error').classList.add('visible');throw e;}
renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;
renderer.shadowMap.enabled=false;stage.appendChild(renderer.domElement);renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','3D 사무실. 캐릭터와 상태는 아래 선택 상자로도 바꿀 수 있습니다.');
scene.add(new THREE.HemisphereLight('#FFF7E5','#B1BDA3',2.1));
const sun=new THREE.DirectionalLight('#FFF0D4',2.7);sun.position.set(-8,17,12);scene.add(sun);
const fill=new THREE.DirectionalLight('#DFE9D9',.7);fill.position.set(10,6,-8);scene.add(fill);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enablePan=false;controls.enableDamping=false;controls.minPolarAngle=.57;controls.maxPolarAngle=.98;
controls.minAzimuthAngle=.08;controls.maxAzimuthAngle=.73;controls.rotateSpeed=.40;controls.zoomSpeed=.55;controls.minZoom=.83;controls.maxZoom=1.65;
const s=new Shapes(scene),charactersRoot=s.group(scene);
const params=new URLSearchParams(location.search);
let roster,skinSelections;
try {roster=rosterFor(params);skinSelections=selectionsFor(roster,params);}
catch(error){$('skin-notice').textContent=error.message;$('skin-notice').hidden=false;roster=rosterFor(new URLSearchParams());skinSelections=selectionsFor(roster,new URLSearchParams());}
const chars=[];
for(const [index,def] of roster.entries()){
  const ctx={shapes:s,parent:charactersRoot,def,index};let skin;
  try {skin=await buildSkin(ctx,skinSelections.get(def.id));}
  catch(error){skin=buildProcedural(ctx);$('skin-notice').textContent=`${def.name}: 스킨을 읽지 못해 기본 캐릭터로 표시합니다.`;$('skin-notice').hidden=false;}
  skin.setState(def.state);
  const status=buildStatusLayer(s,skin.anchors.head,index*.81);
  chars.push({...def,index,skin,status,root:skin.root,bodyHeight:skin.bodyHeight,bodyCenterY:skin.bodyCenterY,bodyBottom:skin.bodyBottom});
}
$('stage').setAttribute('aria-label',`${chars.length}명 직원이 있는 3D 연구소`);
document.querySelector('.scene-note b').textContent=`${chars.length} COLLEAGUES · 01 FLOOR`;
document.querySelector('.edition span').textContent=`한 층, ${chars.length}명의 동료`;
const byId=new Map(chars.map(c=>[c.id,c]));chars.forEach(c=>c.root.userData.agentId=c.id);
const {room,leds}=buildOffice(s,scene,roster);
const selection=s.group(scene);s.ring(selection,C.gold,[0,.13,0],[.89,.89,.89],[-Math.PI/2,0,0]);
s.build();


const labels=new Map();
chars.forEach(c=>{const label=document.createElement('div');label.className='tag';label.dataset.agent=c.id;stage.appendChild(label);labels.set(c.id,label);const opt=document.createElement('option');opt.value=c.id;opt.textContent=c.name;$('character').appendChild(opt);});
STATES.forEach(state=>{const opt=document.createElement('option');opt.value=state;opt.textContent=STATE_LABELS[state];$('state').appendChild(opt);});
const detail={queued:'자기 차례를 기다리며 천천히 숨을 쉽니다.',working:'손을 움직이며 맡은 업무를 진행합니다.',waiting:'<b>빨간 깃발 = PI 승인 대기.</b> 손을 들고 기다립니다.',hibernating:'HPC 작업을 기다리는 중입니다. 세션은 쉬고 있습니다.',done:'일을 마치고 작은 박수를 보냅니다.',error:'땀방울 = 문제가 생겼습니다. 확인이 필요합니다.'};
function updatePanel(){const c=byId.get(selected);document.body.classList.toggle('focused-mode',focused);$('character').value=selected;$('state').value=c.state;$('agent-name').textContent=c.name;$('agent-role').textContent=c.role;$('agent-number').textContent=String(c.index+1).padStart(2,'0');$('state-detail').innerHTML=detail[c.state];$('focus').textContent=focused?'전체 보기':'가까이 보기';
  const waiting=chars.filter(ch=>ch.state==='waiting');$('approval-chip').hidden=!waiting.length;$('approval-chip').textContent=`승인 대기 ${waiting.length}`;
  for(const ch of chars){const label=labels.get(ch.id);label.className=`tag${ch.id===selected?' selected':''}${view==='gallery'?' gallery':''}`;label.innerHTML=view==='gallery'?`<strong>${String(ch.index+1).padStart(2,'0')} · ${ch.badge?`${ch.species} ${ch.badge.letter}`:ch.species}</strong><small class="gallery-role">${ch.prop}</small>`:`<span><i class="state-dot" style="background:${STATE_COLORS[ch.state]}"></i>${ch.badge?`${ch.species} ${ch.badge.letter}`:ch.species}</span><small>${STATE_LABELS[ch.state]}</small>`;}
}
function setState(id,state){if(!byId.has(id))throw new RangeError(`Unknown character: ${id}`);if(!STATES.includes(state))throw new RangeError(`Unknown agent.status: ${state}`);byId.get(id).state=state;byId.get(id).skin.setState(state);updatePanel();requestRender();return state;}
function select(id){if(!byId.has(id))throw new RangeError(`Unknown character: ${id}`);selected=id;updatePanel();if(focused&&view==='office')frameCamera();requestRender();}
$('character').addEventListener('change',e=>select(e.target.value));$('state').addEventListener('change',e=>setState(selected,e.target.value));
$('randomize').addEventListener('click',()=>{chars.forEach(c=>{c.state=STATES[Math.floor(Math.random()*STATES.length)];c.skin.setState(c.state);});updatePanel();requestRender();});
$('focus').addEventListener('click',()=>{if(view==='gallery')setView('office');focused=!focused;frameCamera();updatePanel();requestRender();});
let lastApproval=null;
$('approval-chip').addEventListener('click',()=>{
  const waiting=chars.filter(c=>c.state==='waiting');if(!waiting.length)return;
  const next=waiting[(waiting.findIndex(c=>c.id===lastApproval)+1)%waiting.length];
  if(view!=='office')setView('office');focused=true;lastApproval=next.id;select(next.id);
});
$('office-view').addEventListener('click',()=>setView('office'));$('gallery-view').addEventListener('click',()=>setView('gallery'));
$('silhouette').addEventListener('click',()=>{silhouette=!silhouette;s.silhouette(silhouette);$('silhouette').setAttribute('aria-pressed',String(silhouette));requestRender();});
$('reset-view').addEventListener('click',()=>{focused=false;frameCamera();updatePanel();requestRender();});
function setView(next){view=next;focused=false;silhouette=false;s.silhouette(false);$('silhouette').hidden=view!=='gallery';$('silhouette').setAttribute('aria-pressed','false');document.body.classList.toggle('gallery-mode',view==='gallery');$('office-view').setAttribute('aria-pressed',String(view==='office'));$('gallery-view').setAttribute('aria-pressed',String(view==='gallery'));$('hint').textContent=view==='gallery'?'같은 3D 모델 · 윤곽 버튼으로 색 없이 비교 · 몸체 높이 기준':'직원을 누르면 선택 · 드래그로 회전 · 스크롤 / 두 손가락으로 줌';room.visible=view==='office';selection.visible=view==='office';resize();updatePanel();requestRender();}
let width=1,height=1,ppu=40;
function frameCamera(){
  const aspect=width/height;camera.zoom=1;
  if(view==='gallery'){
    controls.enabled=false;
    const cols=width<700?3:6,rows=Math.ceil(chars.length/cols),rowPitch=width<700?3.17:4.2;
    ppu=Math.min(43,width/(cols*2.82),height/(rows*rowPitch));
    const bodyPixels=Math.min(80,height/rows-36,width/cols-25);
    camera.left=-width/(2*ppu);camera.right=-camera.left;camera.top=height/(2*ppu);camera.bottom=-camera.top;
    camera.position.set(0,0,35);camera.lookAt(0,0,0);
    chars.forEach((c,i)=>{const scale=bodyPixels/(ppu*c.bodyHeight);c.root.scale.setScalar(scale);c.root.position.set((i%cols-(cols-1)/2)*2.82,((rows-1)/2-Math.floor(i/cols))*rowPitch-c.bodyCenterY*scale-.08,0);c.root.rotation.y=0;});
    $('gallery-scale').textContent=`몸체 ${Math.round(bodyPixels)}px · 상태 표시는 별도`;
  }else{
    controls.enabled=true;chars.forEach(c=>{c.root.position.set(c.seat[0],0,c.seat[1]);c.root.rotation.y=0;c.root.scale.setScalar(1);});
    const target=focused?new THREE.Vector3(byId.get(selected).seat[0],1.20,byId.get(selected).seat[1]+.20):new THREE.Vector3(0,.68,.1);
    const span=focused?Math.max(4.9,5.0/aspect):Math.max(16.2,22.6/aspect);
    camera.top=span/2;camera.bottom=-span/2;camera.left=-span*aspect/2;camera.right=span*aspect/2;
    camera.position.copy(target).add(new THREE.Vector3(13,20,25));controls.target.copy(target);camera.lookAt(target);controls.update();
  }
  camera.updateProjectionMatrix();
}
function resize(){const box=stage.getBoundingClientRect();width=Math.max(1,box.width);height=Math.max(1,box.height);renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.setSize(width,height);frameCamera();requestRender();}
new ResizeObserver(resize).observe(stage);
const projection=new THREE.Vector3();
function placeLabels(){
  const c=byId.get(selected);selection.position.set(c.seat[0],0,c.seat[1]);
  chars.forEach(ch=>{
    if(view==='gallery')projection.set(0,ch.bodyBottom-.17,0).applyMatrix4(ch.root.matrixWorld);
    else ch.skin.anchors.label.getWorldPosition(projection);
    projection.project(camera);
    const x=(projection.x*.5+.5)*width,y=(-projection.y*.5+.5)*height,label=labels.get(ch.id);
    const show=window.innerWidth>480||ch.id===selected||ch.state==='waiting';
    label.style.display=!show||projection.z>1||projection.z< -1||x<0||x>width||y<0||y>height?'none':'block';
    const half=label.offsetWidth/2+6;label.style.left=`${Math.max(half,Math.min(width-half,x))}px`;label.style.top=`${y}px`;
  });
}
let pointerStart;
renderer.domElement.addEventListener('pointerdown',e=>{pointerStart=[e.clientX,e.clientY];});
renderer.domElement.addEventListener('pointerup',e=>{
  if(!pointerStart||Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>7)return;
  const r=stage.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,dist=Infinity;
  chars.forEach(c=>{c.skin.anchors.head.getWorldPosition(projection).project(camera);const d=Math.hypot((projection.x*.5+.5)*width-x,(-projection.y*.5+.5)*height-y);if(d<dist){best=c;dist=d;}});
  if(best&&dist<(view==='gallery'?60:focused?130:50))select(best.id);
});

// Rendering pauses on hidden documents; reduced-motion renders only on changes.
let raf=0,lastTime=0,elapsed=0,frames=0,sampleTime=0,fps=0,lastUi=0,contextLost=false,totalFrames=0;
function render(now){
  raf=0;if(document.hidden||contextLost)return;
  const frameSeconds=lastTime?(now-lastTime)/1000:0;const dt=Math.min(frameSeconds,.1);lastTime=now;elapsed+=dt;
  chars.forEach(c=>{c.skin.update(dt,elapsed,{reduced,silhouette});c.status.update(c.state,elapsed,reduced,silhouette);});
  leds.forEach((led,i)=>{const f=reduced?1:.55+.45*(.5+.5*Math.sin(elapsed*4+i*1.9));led.scale.set(.041*f,.041*f,.02);});
  s.sync();placeLabels();renderer.render(scene,camera);
  totalFrames++;frames++;sampleTime+=frameSeconds;if(sampleTime>=1){fps=Math.round(frames/sampleTime);frames=0;sampleTime=0;}
  if(now-lastUi>600){const stats=getStats();$('metrics').textContent=`${stats.drawCalls} calls · ${Math.round(stats.triangles/1000)}k tris · ${reduced?'정지':`${stats.fps} fps`}`;lastUi=now;}
  if(!reduced)raf=requestAnimationFrame(render);
}
function requestRender(){if(!raf&&!document.hidden&&!contextLost)raf=requestAnimationFrame(render);}
function getStats(){return {drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,fps:document.hidden||reduced?0:fps};}
controls.addEventListener('change',requestRender);
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(raf)cancelAnimationFrame(raf);raf=0;lastTime=0;frames=0;sampleTime=0;fps=0;}else requestRender();});
reducedQuery.addEventListener('change',e=>{reduced=e.matches;lastTime=0;frames=0;sampleTime=0;fps=0;requestRender();});
renderer.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();contextLost=true;if(raf)cancelAnimationFrame(raf);raf=0;$('error').textContent='그래픽 연결이 잠시 끊겼습니다. 복구를 기다립니다.';$('error').classList.add('visible');});
renderer.domElement.addEventListener('webglcontextrestored',()=>{contextLost=false;$('error').classList.remove('visible');lastTime=0;requestRender();});
window.__labhq3d={characters:chars.map(c=>c.id),setState,getState(id){if(!byId.has(id))throw new RangeError(`Unknown character: ${id}`);return byId.get(id).state;},stats:getStats,
  snapshot(){return {selected,focused,view,frames:totalFrames,primitives:s.parts.length,
    characters:chars.map(c=>({id:c.id,state:c.state,skin:c.skin.kind,budget:c.skin.budget,seat:[...c.seat],badge:c.badge||null,
      markers:c.status.visible(),clip:c.skin.clip||null,head:c.skin.anchors.head.getWorldPosition(new THREE.Vector3()).toArray(),
      handL:c.skin.anchors.handL.getWorldPosition(new THREE.Vector3()).toArray(),
      handRotation:c.skin.anchors.handL.getWorldQuaternion(new THREE.Quaternion()).toArray(),
      screen:c.skin.anchors.head.getWorldPosition(new THREE.Vector3()).project(camera).toArray(),
      labelVisible:labels.get(c.id).style.display!=='none'}))};}
};
// Local verification can inspect counts without a renderer or scene escape hatch.
renderer.domElement.dataset.primitives=String(s.parts.length);
updatePanel();resize();requestRender();
