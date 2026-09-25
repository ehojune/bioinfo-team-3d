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

// Attachments follow the rig's world pose, not its model/export units. Keep
// source nodes separate: fallback poses must still move the actual hands.
function sceneAnchors(scene,sources) {
  const anchors={},position=new THREE.Vector3(),rotation=new THREE.Quaternion();
  const discardedScale=new THREE.Vector3(),unit=new THREE.Vector3(1,1,1);
  const inverseParent=new THREE.Matrix4(),world=new THREE.Matrix4();
  for(const key of Object.keys(sources)){
    const proxy=new THREE.Group();proxy.name=`attachment:${key}`;
    proxy.matrixAutoUpdate=false;scene.add(proxy);anchors[key]=proxy;
  }
  return {anchors,
    sync(){
      scene.updateWorldMatrix(true,false);inverseParent.copy(scene.matrixWorld).invert();
      for(const [key,source] of Object.entries(sources)){
        source.updateWorldMatrix(true,false);
        source.matrixWorld.decompose(position,rotation,discardedScale);
        world.compose(position,rotation.normalize(),unit);
        const proxy=anchors[key];proxy.matrix.multiplyMatrices(inverseParent,world);proxy.matrixWorldNeedsUpdate=true;
        proxy.visible=true;for(let node=source;node;node=node.parent)if(!node.visible){proxy.visible=false;break;}
      }
    },
    dispose(){Object.values(anchors).forEach(proxy=>proxy.removeFromParent());}
  };
}

// A named attachment can be empty. Only animate nodes that own visible meshes
// or bones with nonzero skin weights (including weighted descendant bones).
function deformingNodes(model) {
  const nodes=new Set();
  const addAncestors=node=>{for(let n=node;n;n=n.parent){nodes.add(n);if(n===model)break;}};
  model.traverseVisible(mesh=>{
    if(!mesh.isMesh||!mesh.geometry.attributes.position?.count)return;
    const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material];
    if(!materials.some(m=>m.visible&&m.opacity>0))return;
    addAncestors(mesh);
    if(!mesh.isSkinnedMesh)return;
    const indices=mesh.geometry.attributes.skinIndex,weights=mesh.geometry.attributes.skinWeight;
    if(!indices||!weights)return;
    for(let i=0;i<weights.count;i++)for(let j=0;j<4;j++){
      if(weights.getComponent(i,j)>0)addAncestors(mesh.skeleton.bones[indices.getComponent(i,j)]);
    }
  });
  return nodes;
}

function inferHand(model,side,deforming) {
  let best=null,rank=Infinity;
  model.traverse(node=>{
    if(!deforming.has(node))return;
    const words=node.name.replace(/([a-z])([A-Z])/g,'$1 $2').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    const compact=words.filter(w=>!['bone','joint','jnt','def'].includes(w)).join('');
    if(/finger|thumb|index|middle|ring|pinky|little/.test(compact))return;
    const left=words.includes('l')||compact.includes('left')||/^l(?:hand|wrist|forearm|upperarm|arm)/.test(compact)||/(?:hand|wrist|forearm|upperarm|arm)l\d*$/.test(compact);
    const right=words.includes('r')||compact.includes('right')||/^r(?:hand|wrist|forearm|upperarm|arm)/.test(compact)||/(?:hand|wrist|forearm|upperarm|arm)r\d*$/.test(compact);
    if(left===right||(side==='handL'?!left:!right))return;
    const part=compact.replace(/left|right/g,'').match(/(forearm|upperarm|hand|wrist|arm)[lr]?\d*$/)?.[1];
    const score=['hand','wrist','forearm','upperarm','arm'].indexOf(part);
    if(score>=0&&score<rank){best=node;rank=score;}
  });
  return best;
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
  const estimated={head:[center.x,box.max.y-size.y*.25,center.z],handL:[box.min.x,box.min.y+size.y*.5,center.z],handR:[box.max.x,box.min.y+size.y*.5,center.z]};
  const sources={},handRest=[],handRoutes={},deforming=deformingNodes(model);
  for(const [key,name] of Object.entries({head:'anchor_head',handL:'anchor_hand_l',handR:'anchor_hand_r'})){
    const node=model.getObjectByName(name);
    const hand=key!=='head',target=hand?(node&&deforming.has(node)?node:inferHand(model,key,deforming)):null;
    if(node||target)sources[key]=node||target;
    else {
      const anchor=new THREE.Group();anchor.name=`source:${key}`;
      anchor.position.fromArray(estimated[key]);transform.worldToLocal(anchor.position);
      transform.add(anchor);sources[key]=anchor;
    }
    if(hand){
      handRest.push({key,node:target,position:target?.position.clone(),quaternion:target?.quaternion.clone()});
      handRoutes[key]={route:target?(target===node?'anchor':'inferred'):'body',node:target?.name||null};
    }
  }
  sources.label=new THREE.Group();sources.label.name='source:label';sources.label.position.set(0,.60,1.60);root.add(sources.label);
  const attachments=sceneAnchors(ctx.shapes.scene,sources),anchors=attachments.anchors;
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
  const bodyFallback=handRest.some(h=>!h.node);
  let motion={state,route:'body',hands:handRoutes};
  const api={root,anchors,kind:'gltf',budget:{triangles,drawCalls,shared:false},get clip(){return action?.getClip().name||null;},get motion(){return structuredClone(motion);},
    bodyHeight:size.y,bodyCenterY:center.y,bodyBottom:box.min.y,
    setState(next){
      stateGuard(next);if(state===next&&action)return;
      mixer.stopAllAction();state=next;pose.position.set(0,0,0);pose.rotation.set(0,0,0);
      handRest.forEach(h=>{if(h.node){h.node.position.copy(h.position);h.node.quaternion.copy(h.quaternion);}});
      const clip=THREE.AnimationClip.findByName(gltf.animations,entry.clips?.[next]||next);
      action=clip?mixer.clipAction(clip):null;if(action)action.reset().play();
      attachments.sync();
    },
    update(dt,t,{reduced=false,silhouette=false}={}){
      if(disposed)return;
      if(outline!==silhouette){outline=silhouette;originals.forEach((m,node)=>{node.material=outline?ink:m;});}
      const a=reduced?0:1,phase=t+ctx.index*.81;
      if(action&&!silhouette){motion={state,route:'clip',hands:handRoutes};pose.position.set(0,0,0);pose.rotation.set(0,0,0);mixer.update(reduced?0:dt);attachments.sync();return;}
      pose.position.set(0,a*.017*Math.sin(phase*2),0);pose.rotation.set(0,0,0);
      handRest.forEach(h=>{if(h.node){h.node.position.copy(h.position);h.node.quaternion.copy(h.quaternion);}});
      const fallback=silhouette?'queued':state;
      motion={state:fallback,route:!bodyFallback&&['working','waiting','done'].includes(fallback)?'hands':'body',hands:handRoutes};
      if(fallback==='working'){
        pose.rotation.x=bodyFallback ? .13+a*.10*Math.sin(phase*4) : .06+a*.03*Math.sin(phase*4);
        if(bodyFallback)pose.position.z=a*.055*Math.sin(phase*4);
        handRest.forEach((h,i)=>h.node?.rotateX(-.4+a*.2*Math.sin(phase*8+i*Math.PI)));
      }
      if(fallback==='waiting'){
        pose.rotation.z=bodyFallback?-.16:-.08;if(bodyFallback)pose.position.y+=.10;
        handRest[0].node?.rotateZ(-2.4);
      }
      if(fallback==='hibernating'){pose.rotation.x=.16;pose.rotation.z=.10;pose.position.y=a*.016*Math.sin(phase*.85);}
      if(fallback==='done'){
        pose.position.y=bodyFallback ? .07+a*.09*(.5+.5*Math.sin(phase*5)) : a*.045*Math.sin(phase*5);
        if(bodyFallback)pose.rotation.x=-.06;
        handRest.forEach((h,i)=>h.node?.rotateZ((i?1:-1)*1.2));
      }
      if(fallback==='error')pose.rotation.z=reduced ? .045 : .025+.065*Math.sin(phase*7);
      attachments.sync();
    },
    dispose(){if(disposed)return;disposed=true;mixer.stopAllAction();mixer.uncacheRoot(model);attachments.dispose();root.removeFromParent();
      geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());skeletons.forEach(s=>s.dispose());ink.dispose();}
  };
  ctx.parent.add(root);api.setState(ctx.def.state);return api;
}

export async function buildSkin(ctx,entry) {
  if(entry?.type==='gltf')return buildGLTF(ctx,entry);
  if(entry?.type!=='procedural')throw new RangeError('Unknown skin type');
  return buildProcedural(ctx);
}
