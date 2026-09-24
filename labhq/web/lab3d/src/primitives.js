import * as THREE from 'three';

// A colored primitive is a transform, not a draw call. Shared batches carry the
// whole office, including animated parts; no per-character material duplication.
const G = {
  box: new THREE.BoxGeometry(1,1,1),
  ball: new THREE.SphereGeometry(1,12,8),
  cylinder: new THREE.CylinderGeometry(1,1,1,12),
  cone: new THREE.ConeGeometry(1,1,10),
  ring: new THREE.TorusGeometry(1,.13,5,20),
  disc: new THREE.CircleGeometry(1,24),
};
const colorCache = new Map();
const color = hex => { if(!colorCache.has(hex)) colorCache.set(hex,new THREE.Color(hex)); return colorCache.get(hex); };
const dummy = new THREE.Object3D();

export class Shapes {
  constructor(scene) {
    this.scene=scene; this.parts=[]; this.batches=[];
    this.material=new THREE.MeshStandardMaterial({roughness:.93,metalness:0,flatShading:false});
    this.silhouetteMaterial=new THREE.MeshBasicMaterial({color:C.ink});
    const ink=new THREE.Color(C.ink);
    this.silhouetteMaterial.onBeforeCompile=shader=>{shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`diffuseColor.rgb = vec3(${ink.r},${ink.g},${ink.b});`);};
  }
  group(parent,pos=[0,0,0],rot=[0,0,0]) { const g=new THREE.Group();g.position.set(...pos);g.rotation.set(...rot);parent.add(g);return g; }
  part(parent,type,hex,pos=[0,0,0],scale=[1,1,1],rot=[0,0,0]) {
    const p=new THREE.Object3D();p.position.set(...pos);p.scale.set(...scale);p.rotation.set(...rot);parent.add(p);
    this.parts.push({node:p,type,hex});return p;
  }
  box(p,c,xyz,s,r){return this.part(p,'box',c,xyz,s,r)}
  ball(p,c,xyz,s,r){return this.part(p,'ball',c,xyz,s,r)}
  cone(p,c,xyz,s,r){return this.part(p,'cone',c,xyz,s,r)}
  cyl(p,c,xyz,s,r){return this.part(p,'cylinder',c,xyz,s,r)}
  ring(p,c,xyz,s,r){return this.part(p,'ring',c,xyz,s,r)}
  disc(p,c,xyz,s,r){return this.part(p,'disc',c,xyz,s,r)}
  bar(parent,c,a,b,r=.04) {
    const start=new THREE.Vector3(...a),end=new THREE.Vector3(...b),d=end.clone().sub(start);
    const n=this.cyl(parent,c,start.add(end).multiplyScalar(.5).toArray(),[r,d.length(),r]);n.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),d.normalize());return n;
  }
  poly(parent,name,c,vertices) {
    if(!G[name]){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(vertices.flat(),3));g.computeVertexNormals();G[name]=g;}
    return this.part(parent,name,c);
  }
  build() {
    for(const {mesh} of this.batches){mesh.removeFromParent();mesh.dispose();}
    this.batches=[];
    for(const [type,geometry] of Object.entries(G)) {
      const parts=this.parts.filter(p=>p.type===type);if(!parts.length)continue;
      const mesh=new THREE.InstancedMesh(geometry,this.material,parts.length);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.frustumCulled=false;mesh.name=`batch:${type}`;
      parts.forEach((p,i)=>mesh.setColorAt(i,color(p.hex)));mesh.instanceColor.needsUpdate=true;
      this.scene.add(mesh);this.batches.push({mesh,parts});
    }
    this.sync();
  }
  sync() {
    this.scene.updateMatrixWorld(true);
    for(const {mesh,parts} of this.batches) {
      parts.forEach((p,i)=>{
        let visible=true;for(let n=p.node;n;n=n.parent){if(!n.visible){visible=false;break;}}
        if(visible)mesh.setMatrixAt(i,p.node.matrixWorld);
        else {dummy.position.set(0,-1000,0);dummy.scale.set(0,0,0);dummy.updateMatrix();mesh.setMatrixAt(i,dummy.matrix);}
      });mesh.instanceMatrix.needsUpdate=true;
    }
  }
  bounds(group) {
    this.scene.updateMatrixWorld(true);const box=new THREE.Box3();
    for(const p of this.parts){let n=p.node;while(n&&n!==group)n=n.parent;if(!n)continue;
      const geo=G[p.type];if(!geo.boundingBox)geo.computeBoundingBox();box.union(geo.boundingBox.clone().applyMatrix4(p.node.matrixWorld));
    }return box;
  }
  ownedParts(group) {
    return this.parts.filter(p=>{for(let n=p.node;n;n=n.parent)if(n===group)return true;return false;});
  }
  budget(group) {
    const parts=this.ownedParts(group);
    return {triangles:parts.reduce((n,p)=>n+(G[p.type].index?.count||G[p.type].attributes.position.count)/3,0),drawCalls:new Set(parts.map(p=>p.type)).size,shared:true};
  }
  remove(group) {
    const owned=new Set(this.ownedParts(group));
    this.parts=this.parts.filter(p=>!owned.has(p));group.removeFromParent();
    if(this.batches.length)this.build();
  }
  silhouette(enabled){for(const {mesh} of this.batches)mesh.material=enabled?this.silhouetteMaterial:this.material;}
}

export function canvasTexture(w,h,paint){const c=document.createElement('canvas');c.width=w;c.height=h;paint(c.getContext('2d'),w,h);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return t;}

// All signage uses one generated atlas and one merged BufferGeometry.
export class Signage {
  constructor(){this.items=[];}
  add(text,pos,width,height,opts={}){this.items.push({text,pos,width,height,...opts});}
  build(parent){
    const cellW=512,cellH=160,cols=4,rows=Math.ceil(this.items.length/cols);
    const tex=canvasTexture(cellW*cols,cellH*rows,(ctx)=>{
      this.items.forEach((item,i)=>{const x=(i%cols)*cellW,y=Math.floor(i/cols)*cellH;
        ctx.fillStyle=item.bg||'#f5f1e5';ctx.fillRect(x,y,cellW,cellH);ctx.fillStyle=item.fg||'#466052';ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`600 ${item.font||36}px system-ui, sans-serif`;ctx.fillText(item.text,x+cellW/2,y+cellH/2,cellW-30);
      });
    });
    const vertices=[],uv=[],normals=[];
    this.items.forEach((it,i)=>{
      const g=new THREE.PlaneGeometry(it.width,it.height).toNonIndexed();
      const m=new THREE.Matrix4().compose(new THREE.Vector3(...it.pos),new THREE.Quaternion().setFromEuler(new THREE.Euler(...(it.rot||[0,0,0]))),new THREE.Vector3(1,1,1));g.applyMatrix4(m);
      vertices.push(...g.attributes.position.array);normals.push(...g.attributes.normal.array);
      for(let j=0;j<g.attributes.uv.count;j++){uv.push(((i%cols)+g.attributes.uv.getX(j))/cols,1-(Math.floor(i/cols)+1-g.attributes.uv.getY(j))/rows);}g.dispose();
    });
    const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geo.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));geo.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
    const mesh=new THREE.Mesh(geo,new THREE.MeshBasicMaterial({map:tex,side:THREE.DoubleSide}));parent.add(mesh);return mesh;
  }
}

export const C={paper:'#F1EDE3',cream:'#FFF2D8',wood:'#CB9C6E',edge:'#A57854',ink:'#354D47',sage:'#718C73',moss:'#4E6B56',light:'#C4D1B4',rust:'#C88651',gold:'#DDB866',red:'#C25445',blue:'#799AA1',fur:'#B8875A',dark:'#654F3F',plum:'#AC8292',white:'#FFF9EC'};
