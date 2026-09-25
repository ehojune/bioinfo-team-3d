import { C } from './primitives.js';

export const STATES=['queued','working','waiting','hibernating','done','error'];
export const STATE_LABELS={queued:'대기',working:'작업 중',waiting:'승인 대기',hibernating:'HPC 대기',done:'완료',error:'오류'};
export const STATE_COLORS={queued:'#a3a48d',working:'#64856f',waiting:C.red,hibernating:C.blue,done:'#c69c48',error:'#b4755d'};
export const ROSTER=[
  {id:'cso',name:'부엉이 CSO',species:'부엉이',role:'질문 설계 · 업무 배분',prop:'질문 분기 카드',fur:'#A87B50',seat:[-5.6,-3.9],state:'waiting'},
  {id:'chief_of_staff',name:'펭귄 비서실장',species:'펭귄',role:'착수 브리핑',prop:'브리핑 폴더',fur:'#48625B',seat:[-5.6,-.4],state:'working'},
  {id:'biologist',name:'곰 생물학 박사',species:'곰',role:'가설 · 메커니즘',prop:'두꺼운 생물학 도감',fur:'#B88C63',seat:[-1.85,-3.9],state:'queued'},
  {id:'bioinfo-agent',name:'수달 bioinfo',species:'수달',role:'검증된 파이프라인 반복',prop:'연결된 파이프라인 큐브',fur:'#A9764F',seat:[-1.85,3.1],state:'hibernating'},
  {id:'data_steward',name:'다람쥐 데이터 담당',species:'다람쥐',role:'매니페스트 · 체크섬',prop:'체크섬 클립보드',fur:'#C19156',seat:[1.9,-3.9],state:'working'},
  {id:'lit_scout',name:'여우 문헌 스카우트',species:'여우',role:'문헌 검색 · 헤드헌팅',prop:'논문 위 돋보기',fur:'#C88451',seat:[-1.85,-.4],state:'done'},
  {id:'analyst',name:'너구리 분석가',species:'너구리',role:'분석 설계 · 실행',prop:'산점도 보드',fur:'#989788',seat:[1.9,-.4],state:'working'},
  {id:'engineer',name:'문어 엔지니어',species:'문어',role:'파이프라인 · 도구 · 테스트',prop:'렌치',fur:'#AE8292',seat:[1.9,3.1],state:'working'},
  {id:'qc_reviewer',name:'고슴도치 Data QC',species:'고슴도치',role:'PASS / WARN / FAIL 판정',prop:'QC 판정 도장',fur:'#A28763',seat:[5.65,-3.9],state:'error'},
  {id:'sci_reviewer',name:'거북 과학 리뷰어',species:'거북',role:'질문 · 근거 · 철저성 검토',prop:'세 탭 리뷰 문서',fur:'#8FAD7C',seat:[-5.6,3.1],state:'queued'},
  {id:'recruiter',name:'비버 인사팀',species:'비버',role:'Paper2Agent 변환 · 검증',prop:'오퍼레터',fur:'#A38061',seat:[5.65,-.4],state:'done'},
  {id:'contract',name:'병아리 파견직',species:'병아리',role:'논문의 방법 적용',prop:'논문을 접은 종이모자',fur:'#DFC46E',seat:[5.65,3.1],state:'queued'},
];

export function makeCharacter(s,parent,def,index){
  const root=s.group(parent,[def.seat[0],0,def.seat[1]]);root.name=def.id;
  const body=s.group(root),head=s.group(body,[0,1.98,0]);
  const fur=def.fur,dark=C.ink,cream=C.cream;
  let headW=.49,headH=.45,headD=.39,bodyW=.40,bodyH=.48;
  const id=def.archetype||def.id;
  if(id==='cso'){headW=.62;headH=.48;bodyW=.50;}
  if(id==='chief_of_staff'){headW=.36;headH=.40;bodyW=.39;bodyH=.68;head.position.y=2.09;}
  if(id==='biologist'){headW=.55;headH=.50;bodyW=.54;}
  if(id==='bioinfo-agent'){headW=.46;headH=.34;bodyW=.31;bodyH=.57;head.position.y=1.94;}
  if(id==='data_steward'){headW=.39;headH=.37;bodyW=.31;}
  if(id==='lit_scout'){headW=.44;headH=.36;bodyW=.32;}
  if(id==='analyst'){headW=.50;headH=.39;bodyW=.42;}
  if(id==='engineer'){headW=.54;headH=.64;bodyW=.40;bodyH=.22;head.position.y=1.93;}
  if(id==='qc_reviewer'){headW=.42;headH=.32;bodyW=.47;head.position.y=1.81;}
  if(id==='sci_reviewer'){headW=.31;headH=.30;bodyW=.42;bodyH=.42;head.position.set(0,1.95,.26);}
  if(id==='recruiter'){headW=.50;headH=.41;bodyW=.47;}
  if(id==='contract'){headW=.38;headH=.36;bodyW=.33;bodyH=.37;head.position.y=1.72;}
  s.ball(body,fur,[0,1.24,0],[bodyW,bodyH,.35]);
  if(id!=='engineer'){
    const foot=(['chief_of_staff','contract','cso'].includes(id)?C.gold:fur);
    for(const side of [-1,1])s.ball(body,foot,[side*.23,.79,.30],[.20,.12,.27]);
  }
  s.ball(head,fur,[0,0,0],[headW,headH,headD]);
  // Silhouette-specific ears, muzzles, shells, tails and tentacles.
  function roundEars(w,y,r){for(const side of [-1,1]){s.ball(head,fur,[side*w,y,-.015],[r,r,.13]);s.ball(head,C.rust,[side*w,y,.10],[r*.54,r*.54,.025]);}}
  function pointedEars(w,y,h){for(const side of [-1,1]){s.cone(head,fur,[side*w,y,0],[.20,h,.19],[0,0,side*-.17]);s.cone(head,cream,[side*w,y-.02,.135],[.09,h*.6,.035],[0,0,side*-.17]);}}
  function muzzle(w=.26,h=.17,z=.35){s.ball(head,cream,[0,-.12,z],[w,h,.14]);s.ball(head,dark,[0,-.07,z+.13],[.078,.051,.048]);}
  if(id==='cso'){
    pointedEars(.42,.42,.40);
    for(const side of [-1,1]){s.ball(head,cream,[side*.24,.015,.302],[.29,.32,.13]);s.ball(body,C.dark,[side*.47,1.29,0],[.17,.44,.25],[0,0,side*.22]);}
    s.cone(head,C.gold,[0,-.13,.46],[.11,.25,.11],[Math.PI,0,0]);
    s.ball(body,cream,[0,1.30,.30],[.32,.32,.06]);
    for(const x of [-.14,0,.14])s.cone(body,C.wood,[x,1.28,.35],[.055,.12,.024],[0,0,Math.PI]);
  }else if(id==='chief_of_staff'){
    s.ball(body,cream,[0,1.33,.255],[.28,.49,.16]);
    for(const side of [-1,1])s.ball(head,cream,[side*.145,-.005,.28],[.15,.27,.12]);
    s.cone(head,C.gold,[0,-.09,.46],[.105,.28,.09],[Math.PI/2,0,0]);
    s.box(body,C.rust,[0,1.45,.405],[.13,.10,.05],[0,0,.1]);
  }else if(id==='biologist'){
    roundEars(.43,.39,.19);muzzle(.29,.19,.39);
    for(const side of [-1,1])s.box(body,C.white,[side*.25,1.30,.27],[.24,.55,.15],[0,0,side*-.15]);
    s.box(body,C.sage,[-.3,1.28,.36],[.13,.17,.035]);
  }else if(id==='bioinfo-agent'){
    roundEars(.39,.19,.115);muzzle(.30,.15,.34);
    const tail=s.group(body,[-.15,.91,-.22],[0,0,.10]);
    s.ball(tail,fur,[.46,-.06,-.13],[.63,.13,.22],[0,.35,-.13]);s.cone(tail,fur,[1.03,-.1,-.30],[.18,.55,.16],[0,0,-Math.PI/2]);
    s.ball(body,cream,[0,1.35,.28],[.23,.36,.09]);
    for(const side of [-1,1])for(const y of [-.09,-.18])s.bar(head,C.dark,[side*.23,y,.47],[side*.53,y+.035,.47],.013);
  }else if(id==='data_steward'){
    pointedEars(.27,.32,.31);muzzle(.19,.13,.34);
    const tail=s.group(body,[.40,1.20,-.26],[0,-.20,0]);
    s.ball(tail,C.dark,[.25,.27,0],[.32,.56,.27],[0,0,-.35]);s.ball(tail,fur,[.33,.65,.01],[.38,.44,.30]);s.ball(tail,cream,[.38,.71,.265],[.24,.27,.06]);
    s.ball(body,cream,[0,1.28,.29],[.21,.3,.065]);
  }else if(id==='lit_scout'){
    pointedEars(.33,.37,.62);
    for(const side of [-1,1])s.cone(head,cream,[side*.29,-.12,.28],[.20,.53,.18],[0,0,side*Math.PI/2]);
    s.cone(head,fur,[0,-.055,.39],[.19,.43,.15],[Math.PI/2,0,0]);s.ball(head,dark,[0,-.055,.61],[.074,.055,.05]);
    s.ball(body,fur,[.65,1.0,-.08],[.60,.24,.26],[0,-.1,.54]);s.cone(body,cream,[1.03,1.28,-.11],[.25,.48,.25],[0,0,-.9]);
  }else if(id==='analyst'){
    pointedEars(.37,.31,.28);muzzle(.25,.14,.37);
    for(const side of [-1,1])s.ball(head,C.ink,[side*.22,.035,.335],[.23,.15,.095],[0,0,side*.18]);
    const tail=s.group(body,[.4,.88,-.13],[0,0,-.95]);
    for(let j=0;j<6;j++)s.ball(tail,j%2?C.ink:fur,[0,j*.16,0],[.17,.18,.17]);
  }else if(id==='engineer'){
    for(let j=0;j<6;j++){
      const a=(j/5)*Math.PI+.0,x=Math.cos(a),z=Math.sin(a);
      const limb=s.group(body,[x*.24,1.3,z*.18]);
      s.ball(limb,fur,[x*.25,-.22,z*.17],[.14,.33,.15],[0,0,x*-.75]);
      s.ball(limb,fur,[x*.50,-.42,z*.28],[.24,.12,.15]);
      s.ball(limb,C.cream,[x*.58,-.385,z*.31+.10],[.06,.045,.045]);
    }
    s.ball(head,'#BF96A2',[-.14,.27,.15],[.24,.30,.25]);
  }else if(id==='qc_reviewer'){
    for(let j=0;j<11;j++){
      const a=-Math.PI*.72+j/10*Math.PI*1.44;
      s.cone(head,j%2?C.dark:C.edge,[Math.sin(a)*.48,Math.cos(a)*.37,-.15],[.16,.44,.16],[0,0,-a]);
    }
    for(const x of [-.33,0,.33])s.cone(body,C.dark,[x,1.63,-.31],[.20,.60,.22],[.4,0,-x]);
    s.ball(head,cream,[0,-.07,.23],[.36,.25,.23]);s.cone(head,cream,[0,-.1,.51],[.16,.38,.14],[Math.PI/2,0,0]);s.ball(head,C.dark,[0,-.10,.70],[.07,.055,.05]);
  }else if(id==='sci_reviewer'){
    s.ball(body,C.moss,[0,1.32,-.23],[.61,.65,.43]);
    s.ball(body,C.sage,[0,1.57,-.48],[.43,.39,.17]);
    for(const side of [-1,1])s.ball(body,C.light,[side*.42,1.4,-.37],[.14,.30,.19]);
    s.cyl(body,fur,[0,1.72,.25],[.17,.42,.17],[.45,0,0]);
    s.ball(body,cream,[0,1.25,.33],[.31,.35,.075]);
    s.bar(body,C.wood,[-.25,1.28,.385],[.25,1.28,.385],.018);
  }else if(id==='recruiter'){
    roundEars(.41,.28,.135);muzzle(.32,.20,.35);
    for(const side of [-1,1])s.box(head,C.white,[side*.066,-.30,.47],[.108,.17,.065]);
    const tail=s.group(body,[.59,.78,-.14],[0,.15,-.65]);
    s.ball(tail,C.dark,[0,.27,0],[.28,.57,.095]);
    for(let j=0;j<5;j++)s.bar(tail,C.edge,[-.20,j*.17-.07,.081],[.20,j*.17-.07,.081],.018);
    for(const x of [-.12,0,.12])s.bar(tail,C.edge,[x,-.03,.084],[x,.64,.084],.012);
  }else if(id==='contract'){
    s.cone(head,C.rust,[0,-.09,.42],[.095,.23,.095],[Math.PI/2,0,0]);
    const hat=s.group(head,[0,.28,0]);
    s.poly(hat,'paperhat',C.white,[[-.56,0,.25],[.56,0,.25],[0,.54,0],[.56,0,.25],[.56,0,-.25],[0,.54,0],[.56,0,-.25],[-.56,0,-.25],[0,.54,0],[-.56,0,-.25],[-.56,0,.25],[0,.54,0]]);
    s.box(hat,C.cream,[0,.025,.252],[1.18,.14,.045]);
    if(def.badge){
      s.box(hat,def.badge.color,[.35,.07,.282],[.23,.18,.025]);
      // A-D use batched strokes: readable initials without another texture/pass.
      const strokes={A:[[-1,-1,0,1],[0,1,1,-1],[-.6,0,.6,0]],B:[[-1,-1,-1,1],[-1,1,.6,1],[.6,1,.6,-1],[-1,0,.6,0],[-1,-1,.6,-1]],C:[[.7,1,-.7,1],[-.7,1,-.7,-1],[-.7,-1,.7,-1]],D:[[-1,-1,-1,1],[-1,1,.5,.7],[.5,.7,.5,-.7],[.5,-.7,-1,-1]]};
      for(const [x1,y1,x2,y2] of strokes[def.badge.letter])s.bar(hat,C.white,[.35+x1*.055,.07+y1*.06,.303],[.35+x2*.055,.07+y2*.06,.303],.012);
    }
    for(let j=0;j<4;j++)s.box(hat,C.sage,[-.22+j*.055,.12+j*.07,.215-j*.035],[.30-j*.04,.015,.012]);
  }
  // Open and closed eyes use geometry, so sleep remains legible without color.
  const open=s.group(head),closed=s.group(head);closed.visible=false;
  const eyeX=id==='sci_reviewer'?.15:id==='contract'?.145:id==='cso'?.24:id==='chief_of_staff'?.14:.19;
  const eyeZ=id==='cso'?.422:id==='chief_of_staff'?.382:id==='analyst'?.413:id==='qc_reviewer'?.422:.36;
  for(const side of [-1,1]){
    const e=eyeX*side;
    if(id==='analyst')s.ball(open,C.cream,[e,.055,eyeZ],[.068,.079,.031]);
    s.ball(open,dark,[e,.06,eyeZ+.022],[.041,.061,.028]);
    s.ball(open,C.white,[e-.013,.081,eyeZ+.043],[.012,.016,.008]);
    s.bar(closed,dark,[e-.053,.026,eyeZ+.026],[e+.053,.026,eyeZ+.026],.018);
  }
  // Two separately pivoted hands; penguin flippers and octopus arms have distinct proportions.
  const arms=[];
  for(const side of [-1,1]){
    const arm=s.group(body,[side*(bodyW+.025),1.52,.05]);
    const len=id==='chief_of_staff'?.37:id==='engineer'?.31:.26;
    s.ball(arm,fur,[side*.07,-len,.14],[id==='chief_of_staff'?.11:.13,len,.145],[.22,0,side*.12]);
    s.ball(arm,fur,[side*.08,-len*1.7,.19],[.14,.115,.14]);
    arms.push(arm);
  }
  // Each desk has a job object, always placed to the side of the face.
  const prop=s.group(root,def.extraContract?[0,1.02,.62]:[-.86,1.12,.82]);
  function book(c,w=.47,h=.55){s.box(prop,c,[0,h/2,0],[w,h,.16],[0,0,-.13]);s.box(prop,C.cream,[.015,h/2,.095],[w*.85,h*.84,.035],[0,0,-.13]);}
  function lines(n=3){for(let j=0;j<n;j++)s.box(prop,C.sage,[.01,.21+j*.09,.122],[.27,.017,.016],[0,0,-.13]);}
  if(id==='cso'){
    s.bar(prop,C.dark,[0,0,0],[0,.72,0],.025);
    for(const [x,y] of [[-.22,.53],[.22,.66],[0,.28]]){s.bar(prop,C.dark,[0,.27,0],[x,y,0],.016);s.box(prop,C.cream,[x,y,.02],[.27,.18,.045]);s.ball(prop,C.rust,[x,y,.055],[.045,.045,.018]);}
  }else if(id==='chief_of_staff'){book(C.sage,.43,.65);s.box(prop,C.rust,[-.09,.70,0],[.18,.1,.08]);lines();}
  else if(id==='biologist'){book(C.moss,.53,.63);s.box(prop,C.moss,[0,.34,.13],[.3,.32,.02],[0,0,-.13]);s.ball(prop,C.cream,[.01,.34,.15],[.09,.14,.018],[0,0,-.4]);}
  else if(id==='bioinfo-agent'){for(let j=0;j<3;j++){s.box(prop,[C.sage,C.blue,C.gold][j],[j*.19-.2,j*.17+.13,0],[.22,.20,.23]);if(j<2)s.bar(prop,C.ink,[j*.19-.13,j*.17+.19,.04],[j*.19+.02,j*.17+.34,.04],.025);}}
  else if(id==='data_steward'){book(C.edge);s.box(prop,C.ink,[0,.57,.10],[.22,.10,.07]);lines(4);for(let j=0;j<3;j++)s.box(prop,C.moss,[-.15,.22+j*.09,.139],[.04,.04,.017]);}
  else if(id==='lit_scout'){book(C.cream,.47,.14);s.ring(prop,C.dark,[0,.47,.05],[.19,.19,.19]);s.bar(prop,C.dark,[.12,.32,.05],[.31,.1,.05],.04);}
  else if(id==='analyst'){book(C.sage,.48,.56);s.bar(prop,C.ink,[-.16,.16,.13],[-.16,.47,.13],.012);s.bar(prop,C.ink,[-.16,.16,.13],[.18,.16,.13],.012);for(let j=0;j<5;j++)s.ball(prop,C.rust,[-.1+j*.065,.22+j*.047+(j%2)*.05,.15],[.024,.024,.02]);}
  else if(id==='engineer'){s.bar(prop,C.ink,[-.15,.02,0],[.14,.54,0],.068);s.ring(prop,C.ink,[.18,.63,0],[.15,.15,.15]);s.box(prop,C.wood,[.18,.76,.01],[.13,.14,.17]);}
  else if(id==='qc_reviewer'){s.box(prop,C.red,[0,.065,0],[.48,.13,.35]);s.cyl(prop,C.dark,[0,.24,0],[.075,.28,.075]);s.ball(prop,C.dark,[0,.39,0],[.15,.075,.13]);s.box(prop,C.cream,[.24,.012,.27],[.48,.022,.29]);s.box(prop,C.moss,[.24,.03,.27],[.27,.01,.12]);}
  else if(id==='sci_reviewer'){book(C.edge,.50,.59);for(let j=0;j<3;j++)s.box(prop,[C.sage,C.gold,C.blue][j],[-.16+j*.16,.625,.02],[.13,.13,.06]);lines();}
  else if(id==='recruiter'){book(C.cream,.48,.47);s.bar(prop,C.edge,[-.23,.16,.14],[0,.28,.14],.015);s.bar(prop,C.edge,[0,.28,.14],[.23,.16,.14],.015);s.ball(prop,C.red,[0,.25,.16],[.063,.063,.02]);}
  else {s.box(prop,C.sage,[0,.10,0],[.42,.20,.3]);s.box(prop,C.cream,[0,.205,0],[.38,.03,.27]);}
  return {...def,index,root,body,head,headY:head.position.y,open,closed,arms,propGroup:prop,phase:index*.81};
}

export function animateCharacter(c,t,reduced,poseState=c.state){
  const a=reduced?0:1,phase=t+c.phase,wave=Math.sin(phase*2);
  c.body.position.y=a*.017*wave;c.body.rotation.set(0,0,0);c.head.rotation.set(0,0,0);
  c.arms.forEach((arm,i)=>{arm.rotation.set(-.35,0,(i?1:-1)*.10);});
  const state=poseState;
  c.open.visible=state!=='hibernating';c.closed.visible=state==='hibernating';
  if(state==='working'){
    c.arms.forEach((arm,i)=>{arm.rotation.x=-1.02+a*.19*Math.sin(phase*10+i*Math.PI);arm.rotation.z=(i?1:-1)*.20;});
    c.head.rotation.x=.08+a*.025*Math.sin(phase*4);
  }else if(state==='waiting'){
    c.arms[0].rotation.z=-2.65+a*.10*Math.sin(phase*3);c.arms[0].rotation.x=-.15;
  }else if(state==='hibernating'){
    c.head.rotation.x=.25;c.head.rotation.z=.15;c.body.position.y=a*.016*Math.sin(phase*.85);
  }else if(state==='done'){
    c.arms.forEach((arm,i)=>{arm.rotation.x=-1.50;arm.rotation.z=(i?1:-1)*(-.80+a*.38*Math.sin(phase*8));});
  }else if(state==='error'){
    c.head.rotation.z=a*.065*Math.sin(phase*7);
  }
}
