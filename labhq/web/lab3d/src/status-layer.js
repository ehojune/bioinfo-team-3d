import { C } from './primitives.js';

// Common markers attach to every skin's head anchor and remain batched.
export function buildStatusLayer(s,head,phase=0) {
  const root=s.group(head),flag=s.group(root,[.64,.65,0]);
  s.bar(flag,C.edge,[0,-.38,0],[0,.52,0],.025);
  s.box(flag,C.red,[.25,.33,0],[.50,.31,.045],[0,0,.03]);
  s.box(flag,C.cream,[.21,.36,.028],[.04,.12,.015]);s.ball(flag,C.cream,[.21,.25,.03],[.025,.025,.014]);
  const sleep=s.group(root,[.47,.48,.1]);
  for(let j=0;j<2;j++){
    const z=s.group(sleep,[j*.32,j*.31,0]),k=j?1.2:.8;
    s.box(z,C.blue,[0,.12*k,0],[.24*k,.045*k,.05]);s.box(z,C.blue,[0,-.12*k,0],[.24*k,.045*k,.05]);
    s.bar(z,C.blue,[.11*k,.12*k,0],[-.11*k,-.12*k,0],.028*k);
  }
  const sweat=s.group(root,[.58,.45,.1]);
  s.ball(sweat,C.blue,[0,-.06,0],[.09,.12,.06]);s.cone(sweat,C.blue,[0,.06,0],[.085,.21,.06]);
  const check=s.group(root,[.68,.72,0]);
  s.bar(check,C.gold,[-.16,.02,0],[-.02,-.10,0],.044);s.bar(check,C.gold,[-.02,-.10,0],[.24,.20,0],.044);
  const markers={waiting:flag,hibernating:sleep,done:check,error:sweat};
  return {root,
    update(state,t,reduced=false,hidden=false){
      root.visible=!hidden;Object.entries(markers).forEach(([key,node])=>{node.visible=key===state;});
      const a=reduced?0:1,p=t+phase;
      flag.rotation.y=a*.13*Math.sin(p*2.7);sleep.position.y=.48+a*.06*Math.sin(p*1.4);
      check.position.y=.72+a*.045*Math.sin(p*3);sweat.position.y=.45-a*((p*.75)%1)*.32;
    },
    visible(){return root.visible?Object.entries(markers).filter(([,n])=>n.visible).map(([k])=>k):[];},
    dispose(){s.remove(root);}
  };
}
