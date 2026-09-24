import * as THREE from 'three';
import { C, Signage, canvasTexture } from './primitives.js';

export function buildOffice(s,scene,roster){
  const room=s.group(scene);room.name='office';const signs=new Signage();
  s.box(room,'#B6BEA6',[0,-.26,0],[17.7,.50,13.25]);
  s.box(room,'#DADCC8',[0,-.04,0],[17.45,.14,13.0]);
  s.box(room,'#E8E5D3',[0,.025,0],[17.15,.07,12.8]);
  for(let x=-8;x<=8;x+=1)s.box(room,'#DAD8C7',[x,.064,0],[.012,.005,12.65]);
  for(let z=-6;z<=6;z+=1)s.box(room,'#DAD8C7',[0,.065,z],[17.05,.005,.012]);
  // Four quiet work zones retain the current office's organization.
  const rugs=['#CBD4BD','#D7D1B8','#C5D0BF','#DCD3BD'];
  for(let i=0;i<4;i++){
    const x=-5.60+i*3.75;
    s.box(room,rugs[i],[x,.080,-.10],[3.45,.035,10.5]);
    s.box(room,'#E5E4D0',[x,.101,5.00],[3.16,.01,.035]);
    signs.add(['01  DIRECTION','02  RESEARCH','03  DATA','04  QC / PEOPLE'][i],[x,.105,5.48],2.9,.5,{rot:[-Math.PI/2,0,0],bg:'#E8E5D3',font:31});
  }
  // Open cutaway walls, with the back wall kept low enough to frame the people.
  s.box(room,'#D6DCC8',[0,1.81,-6.42],[17.45,3.50,.22]);
  s.box(room,'#ECEBDD',[0,1.81,-6.27],[17.05,3.37,.10]);
  s.box(room,C.wood,[0,.25,-6.15],[17.0,.30,.14]);
  s.box(room,'#D0D8C2',[-8.60,1.04,-.1],[.22,1.99,12.63]);
  s.box(room,C.wood,[-8.60,2.05,-.1],[.27,.08,12.7]);
  // Window: deliberately opaque paper glass, no transmission pass.
  const window=s.group(room,[-8.43,2.25,-3.5],[0,Math.PI/2,0]);
  s.box(window,C.wood,[0,0,0],[3.3,2.13,.16]);s.box(window,'#B8CDC2',[0,0,.10],[3.06,1.90,.09]);
  s.box(window,'#D8E3CD',[-.66,.18,.153],[1.30,1.52,.01]);
  s.box(window,C.cream,[0,0,.19],[.075,1.90,.10]);s.box(window,C.cream,[0,0,.19],[3.06,.075,.10]);
  s.box(window,C.wood,[0,-1.08,.22],[3.57,.13,.48]);
  // Warm patches of window light are flat, cheap geometry.
  for(let i=0;i<3;i++)s.box(room,'#F0E9CF',[-6.95+i*.80,.107,-5.13],[.56,.006,1.17],[0,.45,0]);
  // Each agent owns a chair, desk, keyboard and a deliberately small screen.
  roster.filter(c=>!c.extraContract).forEach((c,i)=>{
    const desk=s.group(room,[c.seat[0],0,c.seat[1]]);desk.userData.agentId=c.id;
    s.box(desk,C.edge,[0,1.055,.92],[2.80,.17,1.28]);
    s.box(desk,'#D8B38B',[0,1.145,.92],[2.85,.08,1.32]);
    for(const x of [-1.08,1.08])for(const z of [.47,1.36])s.cyl(desk,C.edge,[x,.55,z],[.065,1.05,.065],[0,0,x*.025]);
    s.box(desk,'#BAC6AB',[.20,1.19,.80],[1.14,.02,.62]);
    s.box(desk,C.cream,[.12,1.21,.69],[.73,.045,.28]);
    for(let j=0;j<3;j++)s.box(desk,'#A9B5A0',[.12,1.236,.61+j*.077],[.58,.006,.01]);
    const monitor=s.group(desk,[.79,1.20,1.00],[0,-.18,0]);
    s.box(monitor,C.ink,[0,.045,0],[.43,.05,.27]);s.box(monitor,C.ink,[0,.24,0],[.06,.37,.06]);
    s.box(monitor,C.ink,[0,.48,0],[.79,.55,.08]);s.box(monitor,'#9EBCAC',[0,.49,.047],[.69,.45,.022]);
    s.box(monitor,'#C8DAC3',[-.22,.49,.063],[.10,.30,.008]);
    for(let j=0;j<3;j++)s.box(monitor,j===1?C.cream:C.moss,[.055,.60-j*.10,.063],[j===1?.32:.23,.025,.009]);
    s.cyl(desk,C.sage,[0,.76,-.18],[.47,.19,.42]);
    s.ball(desk,C.sage,[0,1.17,-.37],[.45,.40,.12]);
    s.cyl(desk,C.dark,[0,.40,-.17],[.04,.62,.04]);
    s.bar(desk,C.dark,[-.32,.16,-.4],[.32,.16,.05],.045);s.bar(desk,C.dark,[.32,.16,-.4],[-.32,.16,.05],.045);
    s.box(desk,C.cream,[-.73,1.02,1.59],[.78,.18,.045]);
    signs.add(`${String(i+1).padStart(2,'0')}  ${c.species}`, [c.seat[0]-.73,1.025,c.seat[1]+1.618],.74,.155,{font:37,bg:'#FFF2D8'});
    s.cyl(desk,C.cream,[1.06,1.29,.56],[.085,.21,.085]);s.ring(desk,C.cream,[1.17,1.29,.56],[.055,.065,.055],[0,Math.PI/2,0]);
  });
  roster.filter(c=>c.extraContract).forEach(c=>{
    const seat=s.group(room,[c.seat[0],0,c.seat[1]]);
    s.cyl(seat,C.sage,[0,.76,-.18],[.38,.19,.34]);
    s.cyl(seat,C.dark,[0,.40,-.17],[.04,.62,.04]);
    s.box(seat,C.wood,[0,.13,-.17],[.62,.10,.56]);
    s.box(seat,C.cream,[0,.95,.46],[.55,.18,.055]);
    signs.add(c.badge.letter,[c.seat[0],.95,c.seat[1]+.495],.49,.15,{font:45});
  });
  // A single rack; eight running-job LEDs, no real job data in this art study.
  const rack=s.group(room,[-6.83,0,-5.50]);
  s.box(rack,C.ink,[0,1.43,0],[1.40,2.73,.73]);s.box(rack,C.dark,[0,.14,0],[1.55,.18,.86]);
  const leds=[];
  for(let j=0;j<8;j++){
    const y=.40+j*.285;s.box(rack,'#50685C',[0,y,.395],[1.21,.21,.09]);
    for(let k=0;k<4;k++)s.box(rack,'#30473F',[-.24+k*.12,y,.45],[.065,.09,.012]);
    const led=s.ball(rack,C.gold,[.43,y,.47],[.041,.041,.02]);leds.push(led);
  }
  signs.add('HPC / JOB RUNNING',[-6.83,2.63,-5.11],1.13,.21,{bg:'#354D47',fg:'#DFCA84',font:29});
  // DAG board as a generated CanvasTexture, not an imported image.
  s.box(room,C.wood,[-1.38,2.15,-6.08],[5.50,2.04,.15]);
  const dag=canvasTexture(1024,384,(ctx,w,h)=>{
    ctx.fillStyle='#FAF7E9';ctx.fillRect(0,0,w,h);ctx.fillStyle='#466052';ctx.font='600 30px sans-serif';ctx.fillText('TODAY / A QUESTION BECOMES A PLAN',34,47);
    ctx.fillStyle='#8A9681';ctx.font='18px sans-serif';ctx.fillText('REQUEST DAG     ·     DEMO',36,79);
    const nodes=[[95,217,'BRIEF'],[285,217,'PLAN'],[505,151,'RUN'],[505,280,'QC'],[735,217,'REVIEW'],[935,217,'REPORT']];
    const edges=[[0,1],[1,2],[2,3],[3,4],[4,5]];
    ctx.strokeStyle='#A9B399';ctx.lineWidth=4;edges.forEach(([a,b])=>{ctx.beginPath();ctx.moveTo(nodes[a][0],nodes[a][1]);ctx.lineTo(nodes[b][0],nodes[b][1]);ctx.stroke();});
    nodes.forEach(([x,y,label],i)=>{ctx.fillStyle=i===2?'#C88651':i<2?'#718C73':'#E5E9D9';ctx.beginPath();ctx.roundRect(x-61,y-25,122,50,12);ctx.fill();ctx.fillStyle=i<3?'#FFF8E9':'#526650';ctx.font='600 17px sans-serif';ctx.textAlign='center';ctx.fillText(label,x,y+6);});
    ctx.textAlign='left';ctx.fillStyle='#89947F';ctx.font='17px sans-serif';ctx.fillText('01  /  BRIEF → PLAN → EXECUTE → REVIEW → REPORT',36,355);
  });
  const board=new THREE.Mesh(new THREE.PlaneGeometry(5.30,1.84),new THREE.MeshBasicMaterial({map:dag}));board.position.set(-1.38,2.15,-5.991);room.add(board);
  s.box(room,C.edge,[-1.38,1.08,-5.89],[5.63,.10,.31]);
  s.cyl(room,C.rust,[.30,1.16,-5.86],[.033,.36,.033],[0,0,Math.PI/2]);
  // Contract entrance. The open leaf shows where the paper-hat chick arrives.
  s.box(room,C.wood,[6.61,1.48,-6.10],[1.86,2.90,.20]);s.box(room,C.ink,[6.61,1.45,-5.982],[1.54,2.66,.06]);
  const door=s.group(room,[5.91,.11,-5.90],[0,-.60,0]);
  s.box(door,'#D2B188',[.70,1.30,0],[1.40,2.60,.11]);s.box(door,'#B5C8B6',[.70,1.76,.067],[.97,.96,.025]);s.ball(door,C.gold,[1.23,1.12,.12],[.07,.07,.07]);
  signs.add('WELCOME / NEW IDEAS',[6.61,3.13,-6.06],2.10,.28,{font:29});
  s.box(room,C.moss,[6.61,.103,-4.85],[1.87,.035,.53]);
  signs.add('IN',[6.61,.127,-4.86],.50,.29,{rot:[-Math.PI/2,0,0],fg:'#FFF2D8',bg:'#4E6B56',font:60});
  // Wall clock and small notice cards.
  s.cyl(room,C.wood,[3.13,2.59,-6.08],[.37,.11,.37],[Math.PI/2,0,0]);s.cyl(room,C.cream,[3.13,2.59,-6.009],[.31,.045,.31],[Math.PI/2,0,0]);
  s.bar(room,C.ink,[3.13,2.59,-5.974],[3.13,2.79,-5.974],.023);s.bar(room,C.ink,[3.13,2.59,-5.974],[3.30,2.51,-5.974],.023);
  signs.add('MAKE ROOM FOR QUESTIONS',[3.35,1.47,-6.075],2.40,.48,{font:29,bg:'#DADDC9'});
  function plant(x,z,h){
    const p=s.group(room,[x,0,z]);s.cyl(p,C.rust,[0,.29,0],[.29,.47,.29]);s.cyl(p,C.edge,[0,.535,0],[.27,.035,.27]);
    for(let j=0;j<5;j++){const a=j*2.4;const ex=Math.sin(a)*.40,ez=Math.cos(a)*.27,y=.72+h*(.4+j*.1);s.bar(p,C.moss,[0,.49,0],[ex,y,ez],.028);s.ball(p,j%2?C.sage:C.moss,[ex,y,ez],[.16,.32,.08],[.3,a,ex*-.7]);}
  }
  plant(-7.83,5.23,1.20);plant(7.85,5.32,.85);plant(4.33,-5.59,1.50);
  signs.build(room);
  // One instanced transparent blob-shadow pass. No shadow maps or postprocessing.
  const shadowTex=canvasTexture(64,64,(ctx)=>{const g=ctx.createRadialGradient(32,32,2,32,32,30);g.addColorStop(0,'rgba(56,67,42,.27)');g.addColorStop(.6,'rgba(56,67,42,.13)');g.addColorStop(1,'rgba(56,67,42,0)');ctx.fillStyle=g;ctx.fillRect(0,0,64,64);});
  const shadow=new THREE.InstancedMesh(new THREE.PlaneGeometry(1,1),new THREE.MeshBasicMaterial({map:shadowTex,transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1}),roster.length);
  const d=new THREE.Object3D();roster.forEach((c,i)=>{d.position.set(c.seat[0],.112,c.seat[1]+.45);d.rotation.x=-Math.PI/2;d.scale.set(3.55,2.72,1);d.updateMatrix();shadow.setMatrixAt(i,d.matrix);});shadow.frustumCulled=false;room.add(shadow);
  return {room,leds};
}
