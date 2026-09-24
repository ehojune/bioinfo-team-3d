import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { clone } from 'three/addons/utils/SkeletonUtils.js';
import { ROSTER, STATES, makeCharacter, animateCharacter } from './characters.js';

// JSON literals keep this single selection manifest readable by offline tests.
export const SKINS = [
  {"id":"cso","type":"procedural"},
  {"id":"chief_of_staff","type":"procedural"},
  {"id":"biologist","type":"procedural"},
  {"id":"bioinfo-agent","type":"procedural"},
  {"id":"data_steward","type":"procedural"},
  {"id":"lit_scout","type":"procedural"},
  {"id":"analyst","type":"procedural"},
  {"id":"engineer","type":"procedural"},
  {"id":"qc_reviewer","type":"procedural"},
  {"id":"sci_reviewer","type":"procedural"},
  {"id":"recruiter","type":"procedural"},
  {"id":"contract","type":"procedural"}
];
export const MODEL_SKINS = [
  {"id":"placeholder","type":"gltf","src":"./assets/placeholder.gltf","scale":1,"offset":[0,0.67,0],"rotationY":0,"clips":{"queued":"","working":"working","waiting":"","hibernating":"","done":"","error":""}}
];

export function rosterFor(params) {
  const count=Number(params.get('contracts')||1);
  if(!Number.isInteger(count)||count<1||count>4)throw new RangeError('contracts must be 1–4');
  if(count===1)return ROSTER.map(c=>({...c}));
  const base=ROSTER.find(c=>c.id==='contract');
  const seats=[base.seat,[7.75,-3.8],[7.75,-1.6],[7.75,.6]];
  const colors=['#718C73','#AC8292','#799AA1','#C88651'];
  return [...ROSTER.filter(c=>c.id!=='contract').map(c=>({...c})),
    ...Array.from({length:count},(_,i)=>({...base,id:`c_${String.fromCharCode(97+i)}`,archetype:'contract',
      name:`병아리 파견직 ${String.fromCharCode(65+i)}`,seat:seats[i],extraContract:i>0,
      badge:{color:colors[i],letter:String.fromCharCode(65+i)}}))];
}

export function selectionsFor(roster,params) {
  const selected=new Map(roster.map(c=>[c.id,SKINS.find(s=>s.id===(c.archetype||c.id))]));
  for(const item of params.getAll('skin').flatMap(value=>value.split(','))){
    const [id,skinId,...extra]=item.split(':');
    const entry=MODEL_SKINS.find(s=>s.id===skinId);
    if(!selected.has(id)||!entry||extra.length)throw new RangeError(`Unknown skin selection: ${item}`);
    selected.set(id,entry);
  }
  return selected;
}

function stateGuard(state){if(!STATES.includes(state))throw new RangeError(`Unknown agent.status: ${state}`);}

export function buildProcedural(ctx) {
  const {shapes:s,parent,def,index}=ctx;
  const c=makeCharacter(s,parent,def,index);
  const head=s.group(c.head,[0,2-c.headY,0]);
  const label=s.group(c.root,[0,.60,1.60]);
  const box=s.bounds(c.body),height=box.max.y-box.min.y;
  return {
    root:c.root,anchors:{head,handL:c.arms[0],handR:c.arms[1],label},
    budget:s.budget(c.root),kind:'procedural',bodyHeight:height,bodyCenterY:(box.min.y+box.max.y)/2,bodyBottom:box.min.y,
    setState(state){stateGuard(state);c.state=state;},
    update(dt,t,{reduced=false,silhouette=false}={}){
      c.propGroup.visible=!silhouette;animateCharacter(c,t,reduced,silhouette?'queued':c.state);
    },
    dispose(){s.remove(c.root);}
  };
}

// Block remote resources, including nested glTF buffers/textures, before fetch.
function localAssetURL(value) {
  if(/^(data:|blob:)/i.test(value))return value;
  const url=new URL(value,document.baseURI),assets=new URL('./assets/',document.baseURI);
  if(url.origin!==assets.origin||!url.pathname.startsWith(assets.pathname))throw new Error('Skin resources must stay in lab3d/assets');
  return url.href;
}

export async function buildGLTF(ctx,entry) {
  const manager=new THREE.LoadingManager();manager.setURLModifier(localAssetURL);
  const gltf=await new GLTFLoader(manager).loadAsync(localAssetURL(entry.src));
  const root=new THREE.Group(),pose=new THREE.Group(),transform=new THREE.Group();
  const model=clone(gltf.scene);root.add(pose);pose.add(transform);transform.add(model);
  root.name=ctx.def.id;root.position.set(ctx.def.seat[0],0,ctx.def.seat[1]);
  transform.scale.setScalar(entry.scale??1);transform.position.set(...(entry.offset||[0,.67,0]));transform.rotation.y=entry.rotationY||0;
  root.updateMatrixWorld(true);
  const box=new THREE.Box3().setFromObject(transform),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
  const origin=root.position;
  const estimated={head:[center.x,box.max.y-size.y*.25,center.z],handL:[box.min.x,box.min.y+size.y*.5,center.z],handR:[box.max.x,box.min.y+size.y*.5,center.z]};
  const anchors={},handRest=[];
  for(const [key,name] of Object.entries({head:'anchor_head',handL:'anchor_hand_l',handR:'anchor_hand_r'})){
    const node=model.getObjectByName(name);
    if(node)anchors[key]=node;
    else {const anchor=new THREE.Group();anchor.position.fromArray(estimated[key]).sub(origin);pose.add(anchor);anchors[key]=anchor;}
    if(key!=='head')handRest.push({node:anchors[key],position:anchors[key].position.clone(),quaternion:anchors[key].quaternion.clone()});
  }
  anchors.label=new THREE.Group();anchors.label.position.set(0,.60,1.60);root.add(anchors.label);
  const mixer=new THREE.AnimationMixer(model),materials=new Set(),geometries=new Set(),textures=new Set(),skeletons=new Set();
  let triangles=0,drawCalls=0;
  const originals=new Map(),ink=new THREE.MeshBasicMaterial({color:'#354D47'});
  model.traverse(node=>{
    if(!node.isMesh)return;
    geometries.add(node.geometry);if(node.skeleton)skeletons.add(node.skeleton);
    const list=Array.isArray(node.material)?node.material:[node.material];
    originals.set(node,node.material);list.forEach(m=>{materials.add(m);Object.values(m).forEach(v=>{if(v?.isTexture)textures.add(v);});});
    triangles+=(node.geometry.index?.count||node.geometry.attributes.position.count)/3;
    drawCalls+=Array.isArray(node.material)?node.geometry.groups.length:1;
  });
  let state='queued',action=null,outline=false,disposed=false;
  const api={root,anchors,kind:'gltf',budget:{triangles,drawCalls,shared:false},get clip(){return action?.getClip().name||null;},
    bodyHeight:size.y,bodyCenterY:center.y,bodyBottom:box.min.y,
    setState(next){
      stateGuard(next);if(state===next&&action)return;
      mixer.stopAllAction();state=next;pose.position.set(0,0,0);pose.rotation.set(0,0,0);
      handRest.forEach(h=>{h.node.position.copy(h.position);h.node.quaternion.copy(h.quaternion);});
      const clip=THREE.AnimationClip.findByName(gltf.animations,entry.clips?.[next]||next);
      action=clip?mixer.clipAction(clip):null;if(action)action.reset().play();
    },
    update(dt,t,{reduced=false,silhouette=false}={}){
      if(disposed)return;
      if(outline!==silhouette){outline=silhouette;originals.forEach((m,node)=>{node.material=outline?ink:m;});}
      const a=reduced?0:1,phase=t+ctx.index*.81;
      if(action&&!silhouette){pose.position.set(0,0,0);pose.rotation.set(0,0,0);mixer.update(reduced?0:dt);return;}
      pose.position.y=a*.017*Math.sin(phase*2);pose.rotation.set(0,0,0);
      handRest.forEach(h=>{h.node.position.copy(h.position);h.node.quaternion.copy(h.quaternion);});
      const fallback=silhouette?'queued':state;
      if(fallback==='working'){pose.rotation.x=.06+a*.03*Math.sin(phase*4);handRest.forEach((h,i)=>h.node.rotateX(-.4+a*.2*Math.sin(phase*8+i*Math.PI)));}
      if(fallback==='waiting'){pose.rotation.z=-.08;handRest[0].node.rotateZ(-2.4);}
      if(fallback==='hibernating'){pose.rotation.x=.16;pose.rotation.z=.10;pose.position.y=a*.016*Math.sin(phase*.85);}
      if(fallback==='done'){pose.position.y=a*.045*Math.sin(phase*5);handRest.forEach((h,i)=>h.node.rotateZ((i?1:-1)*1.2));}
      if(fallback==='error')pose.rotation.z=a*.065*Math.sin(phase*7);
    },
    dispose(){if(disposed)return;disposed=true;mixer.stopAllAction();mixer.uncacheRoot(model);root.removeFromParent();
      geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());skeletons.forEach(s=>s.dispose());ink.dispose();}
  };
  ctx.parent.add(root);api.setState(ctx.def.state);return api;
}

export async function buildSkin(ctx,entry) {
  if(entry?.type==='gltf')return buildGLTF(ctx,entry);
  if(entry?.type!=='procedural')throw new RangeError('Unknown skin type');
  return buildProcedural(ctx);
}
