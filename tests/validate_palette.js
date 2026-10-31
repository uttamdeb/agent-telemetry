'use strict';
// Equivalent local palette validator: CIEDE2000 after Machado CVD simulations.
const fs=require('node:fs'),path=require('node:path');
const ROOT=path.resolve(__dirname,'..');
const CVD={
  protan:[[.152286,1.052583,-.204868],[.114503,.786281,.099216],[-.003882,-.048116,1.051998]],
  deutan:[[.367322,.860646,-.227968],[.280085,.672501,.047413],[-.01182,.04294,.968881]],
  tritan:[[1.255528,-.076749,-.178779],[-.078411,.930809,.147602],[.004733,.691367,.3039]],
};
function block(css,selector){
  const start=css.indexOf(selector), open=css.indexOf('{',start); if(start<0||open<0) throw Error(`Missing ${selector}`);
  let depth=0; for(let i=open;i<css.length;i++){if(css[i]==='{')depth++;else if(css[i]==='}'&&--depth===0)return css.slice(open+1,i);}
  throw Error(`Unclosed ${selector}`);
}
function linear(v){v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}
function rgb(hex){return [1,3,5].map(i=>linear(parseInt(hex.slice(i,i+2),16)));}
function simulate(color,matrix){return matrix?matrix.map(row=>Math.max(0,Math.min(1,row.reduce((sum,v,i)=>sum+v*color[i],0)))):color;}
function lab([r,g,b]){
  const f=x=>x>.008856451679?Math.cbrt(x):7.787037037*x+16/116;
  const x=f((.4124564*r+.3575761*g+.1804375*b)/.95047);
  const y=f(.2126729*r+.7151522*g+.072175*b);
  const z=f((.0193339*r+.119192*g+.9503041*b)/1.08883);
  return [116*y-16,500*(x-y),200*(y-z)];
}
function de(a,b){
  const [L1,a1,b1]=a,[L2,a2,b2]=b,C1=Math.hypot(a1,b1),C2=Math.hypot(a2,b2),Cb=(C1+C2)/2;
  const G=.5*(1-Math.sqrt(Cb**7/(Cb**7+25**7))),ap1=(1+G)*a1,ap2=(1+G)*a2;
  const c1=Math.hypot(ap1,b1),c2=Math.hypot(ap2,b2),deg=(x,y)=>{const h=Math.atan2(y,x)*180/Math.PI;return h<0?h+360:h;};
  const h1=deg(ap1,b1),h2=deg(ap2,b2);let dh=0;
  if(c1*c2){dh=Math.abs(h2-h1)<=180?h2-h1:h2<=h1?h2-h1+360:h2-h1-360;}
  const dL=L2-L1,dC=c2-c1,dH=2*Math.sqrt(c1*c2)*Math.sin(dh*Math.PI/360),Lb=(L1+L2)/2,Cp=(c1+c2)/2;
  let hb;if(c1*c2===0)hb=h1+h2;else if(Math.abs(h1-h2)<=180)hb=(h1+h2)/2;else if(h1+h2<360)hb=(h1+h2+360)/2;else hb=(h1+h2-360)/2;
  const rad=x=>x*Math.PI/180,T=1-.17*Math.cos(rad(hb-30))+.24*Math.cos(rad(2*hb))+.32*Math.cos(rad(3*hb+6))-.2*Math.cos(rad(4*hb-63));
  const SL=1+.015*(Lb-50)**2/Math.sqrt(20+(Lb-50)**2),SC=1+.045*Cp,SH=1+.015*Cp*T;
  const RT=-2*Math.sqrt(Cp**7/(Cp**7+25**7))*Math.sin(rad(60*Math.exp(-(((hb-275)/25)**2))));
  const l=dL/SL,c=dC/SC,h=dH/SH;return Math.sqrt(l*l+c*c+h*h+RT*c*h);
}
function validatePalette(){
  const css=fs.readFileSync(path.join(ROOT,'static/app.css'),'utf8'),core=fs.readFileSync(path.join(ROOT,'static/core.js'),'utf8');
  const match=core.match(/const ORDER\s*=\s*(\[[^;]+\]);/);if(!match)throw Error('Could not read ORDER');
  const order=JSON.parse(match[1]),themes=[['light',block(css,':root{')],['dark',block(css,':root[data-theme="dark"]{')]];
  let minNormal=Infinity,minCvd=Infinity;
  for(const [theme,source] of themes){
    const colors=order.map(name=>{const found=source.match(new RegExp(`--t-${name}\\s*:\\s*(#[0-9a-fA-F]{6})`));if(!found)throw Error(`${theme}: missing --t-${name}`);return {name,color:rgb(found[1])};});
    for(let i=1;i<colors.length;i++){
      const [prev,next]=[colors[i-1],colors[i]],normal=de(lab(prev.color),lab(next.color));minNormal=Math.min(minNormal,normal);
      if(normal<15)throw Error(`${theme}: ${prev.name}/${next.name} ΔE ${normal.toFixed(2)} < 15`);
      for(const [vision,matrix] of Object.entries(CVD)){const distance=de(lab(simulate(prev.color,matrix)),lab(simulate(next.color,matrix)));minCvd=Math.min(minCvd,distance);if(distance<8)throw Error(`${theme}/${vision}: ${prev.name}/${next.name} ΔE ${distance.toFixed(2)} < 8`);}
    }
  }
  return {sources:order.length,themes:themes.length,minNormal,minCvd};
}
if(require.main===module){const r=validatePalette();process.stdout.write(`Palette ok: ${r.sources} sources × ${r.themes} themes; min normal ΔE ${r.minNormal.toFixed(2)}, min CVD ΔE ${r.minCvd.toFixed(2)}.\n`);}
module.exports={validatePalette};
